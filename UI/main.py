"""Fase 5: pantalla congelada con el texto traducible encima + registro de frases.

Atajo (o botón) -> la pantalla se congela -> eliges la región -> el texto reconocido se dibuja
sobre el original. Con el mouse sobre una palabra ves su significado; un clic la guarda para Anki.
La ventana principal se minimiza al capturar y no vuelve a aparecer sola: la abres tú desde la barra de tareas.
"""
# IMPORTANTE (Windows): las librerías nativas se importan ANTES que PyQt5.
# Importarlas por primera vez dentro de un hilo con Qt ya cargado provoca
# "access violation".
import onnxruntime  # noqa: F401
import ctranslate2  # noqa: F401

import sys

from PyQt5.QtCore import QObject, QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel,
                             QPushButton, QTextEdit, QVBoxLayout, QWidget)


from UI.capture import bgr_to_qpixmap, grab_screen
from Cards.cards import CardStore
from UI.cards_window import CardsWindow
from DB.dictionary import Dictionary
from DB.dictionary_ja import JaDictionary
from DB.ja_nlp import tokenizer as ja_tokenizer
from UI.log_view import LogWindow
from UI.overlay import CaptureOverlay
from DB.phrase_log import PhraseLog
from UI.word_popup import HoverTextEdit, WordPopup
from UI.word_service import WordService
from UI.ocr_worker import OcrWorker
import Translator.japanese as japanese
import Translator.languages as languages

HOTKEY = "<ctrl>+<alt>+o"
HOTKEY_LABEL = "Ctrl+Alt+O"


def make_icon() -> QIcon:
    """Icono sencillo dibujado en código (sin archivos)."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(0, 150, 200))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor(255, 255, 255))
    font = QFont()
    font.setPixelSize(36)
    font.setBold(True)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignCenter, "Aあ")
    p.end()
    return QIcon(pix)


class HotkeyBridge(QObject):
    """Convierte el atajo global (hilo de pynput) en una señal de Qt."""
    triggered = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._listener = None
        try:
            from pynput import keyboard
            self._listener = keyboard.GlobalHotKeys({HOTKEY: self.triggered.emit})
            self._listener.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[aviso] Atajo global no disponible: {exc}")

    def stop(self):
        if self._listener:
            self._listener.stop()


class MainWindow(QWidget):
    process_requested = pyqtSignal(object, bool)   # imagen, unir_lineas
    card_requested = pyqtSignal(int, str, str)     # id de tarjeta, frase, palabra a traducir ("" = ninguna)
    configure_requested = pyqtSignal(str, str)     # idioma del juego, modo de OCR

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR para juegos - Fase 5 (pantalla congelada)")
        self.setWindowIcon(make_icon())
        self.resize(560, 700)
        self.overlay = None
        self.cards_window = None
        self.log_window = None
        self._ocr_info = ""
        self._merge_used = True     # ¿se unieron las líneas en la captura actual?
        self._last_image = None     # recorte de la última captura (para repetir con otro «unir líneas»)
        self._log_id = None         # entrada del registro de la captura en curso
        self._grabbing = False

        # --- idioma del juego y modo de OCR (se recuerdan entre sesiones) ---
        self.settings = QSettings("OCRTranslator", "fase6")
        lang = str(self.settings.value("language", "en"))
        if lang not in languages.PACKS:
            lang = "en"
        mode = str(self.settings.value("ocr_mode", "normal"))
        if mode not in ("normal", "kana"):
            mode = "normal"
        languages.set_active(lang)

        # --- diccionario local, tarjetas, registro y ventanita emergente ---
        self.dictionary = Dictionary()
        self.dictionary_ja = JaDictionary()
        self.store = CardStore()
        self.log = PhraseLog()
        self.popup = WordPopup()
        self.words = WordService(self._dictionary_for(lang), self.store, self.popup, self)
        self.words.saved_changed.connect(self._cards_changed)
        self.words.message.connect(self._on_word_message)
        self.words.card_requested.connect(self.card_requested)

        # --- UI ---
        self.btn_capture = QPushButton(f"Capturar región ({HOTKEY_LABEL})")
        self.btn_capture.setEnabled(False)
        self.btn_cards = QPushButton()
        self.btn_log = QPushButton()
        self.btn_copy_src = QPushButton("Copiar original")
        self.btn_copy_dst = QPushButton("Copiar traducción")
        self.combo_lang = QComboBox()
        for code, pack in languages.PACKS.items():
            self.combo_lang.addItem(pack.label, code)
        self.combo_lang.setCurrentIndex(self.combo_lang.findData(lang))
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("OCR normal (con kanji)", "normal")
        self.combo_mode.addItem("OCR solo kana (todo hiragana/katakana)", "kana")
        self.combo_mode.setCurrentIndex(self.combo_mode.findData(mode))
        self.combo_mode.setToolTip("«Solo kana» corrige más confusiones en juegos sin kanji.")
        self.chk_merge = QCheckBox("Unir líneas partidas (diálogos)")
        self.chk_merge.setChecked(True)
        self.chk_merge.setToolTip(
            "Actívalo para cuadros de diálogo donde una frase ocupa varias líneas.\n"
            "Desactívalo para menús o listas donde cada línea es independiente.\n"
            "Con la captura abierta puedes cambiarlo con la tecla M.")
        self.status = QLabel("Iniciando...")
        self.status.setWordWrap(True)
        self.how_to = QLabel(
            "Tras elegir la región, la pantalla queda congelada con el texto encima. "
            "Esc la cierra. Esta ventana se minimiza al capturar y no vuelve a aparecer sola: "
            "cámbiate a ella desde la barra de tareas cuando quieras ver el registro.")
        self.how_to.setWordWrap(True)
        self.how_to.setStyleSheet("color: gray;")
        self.dict_note = QLabel(
            "⚠ Diccionario no encontrado: ejecuta build_dictionary.py para ver "
            "significados al pasar el mouse sobre una palabra.")
        self.dict_note.setWordWrap(True)
        self.dict_note.setVisible(not self.dictionary.available)
        self.preview = QLabel("(aquí aparece la región capturada)")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(110)
        self.preview.setStyleSheet("border: 1px dashed gray;")
        self.output = HoverTextEdit()
        self.output.setPlaceholderText("Texto original (OCR) de la última captura...")
        self.translation = QTextEdit()
        self.translation.setPlaceholderText("Traducción al español...")

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_capture)
        buttons2 = QHBoxLayout()
        buttons2.addWidget(self.btn_cards)
        buttons2.addWidget(self.btn_log)
        buttons3 = QHBoxLayout()
        buttons3.addWidget(self.btn_copy_src)
        buttons3.addWidget(self.btn_copy_dst)
        layout = QVBoxLayout(self)
        langs = QHBoxLayout()
        langs.addWidget(QLabel("Idioma del juego:"))
        langs.addWidget(self.combo_lang, 1)
        langs.addWidget(self.combo_mode, 1)
        layout.addLayout(langs)
        layout.addLayout(buttons)
        layout.addLayout(buttons2)
        layout.addLayout(buttons3)
        layout.addWidget(self.chk_merge)
        layout.addWidget(self.status)
        layout.addWidget(self.how_to)
        layout.addWidget(self.dict_note)
        layout.addWidget(self.preview)
        self.lbl_original = QLabel()
        self.lbl_translation = QLabel()
        layout.addWidget(self.lbl_original)
        layout.addWidget(self.output, 1)
        layout.addWidget(self.lbl_translation)
        layout.addWidget(self.translation, 1)

        # --- hilo del OCR + traductor (los modelos se cargan una vez, dentro del hilo) ---
        self.thread = QThread()
        self.worker = OcrWorker()
        self.worker.moveToThread(self.thread)
        self.worker.language = lang
        self.worker.ocr_mode = japanese.Mode(mode)
        self.thread.started.connect(self.worker.load)
        self.configure_requested.connect(self.worker.configure)
        self.process_requested.connect(self.worker.process)
        self.card_requested.connect(self.worker.translate_for_card)
        self.worker.status.connect(self.status.setText)
        self.worker.ready.connect(self.on_ready)
        self.worker.result.connect(self.on_result)
        self.worker.translated.connect(self.on_translated)
        self.worker.card_translated.connect(self.on_card_translated)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.thread.start()

        # --- señales de la UI ---
        self.combo_lang.currentIndexChanged.connect(self.on_language_changed)
        self.combo_mode.currentIndexChanged.connect(self.on_mode_changed)
        self.btn_capture.clicked.connect(self.start_capture)
        self.btn_cards.clicked.connect(self.open_cards)
        self.btn_log.clicked.connect(self.open_log)
        self.btn_copy_src.clicked.connect(lambda: self.copy(self.output))
        self.btn_copy_dst.clicked.connect(lambda: self.copy(self.translation))
        self.output.word_hovered.connect(self.words.hover)
        self.output.hover_ended.connect(self.words.hover_left)
        self.output.wheel_moved.connect(self.words.scroll_popup)
        self.output.word_clicked.connect(self.on_word_clicked)
        self.output.textChanged.connect(self.refresh_marks)

        # --- atajo global (opcional) ---
        self.hotkey = HotkeyBridge()
        self.hotkey.triggered.connect(self.start_capture)

        self._apply_language_ui()
        self._update_buttons()

    # --- idioma ---
    def _dictionary_for(self, code):
        return self.dictionary_ja if code == "ja" else self.dictionary

    def _apply_language_ui(self):
        pack = languages.active()
        self.words.dictionary = self._dictionary_for(pack.code)
        self.lbl_original.setText(
            f"Original ({pack.source_name}) — mouse sobre una palabra: significado · clic: guardarla")
        self.lbl_translation.setText(f"Traducción ({pack.target_name})")
        self.translation.setPlaceholderText(f"Traducción al {pack.target_name}...")
        self.combo_mode.setVisible(pack.has_ocr_modes)
        if pack.code == "en":
            self.dict_note.setText("⚠ Diccionario no encontrado: ejecuta build_dictionary.py para ver "
                                   "significados al pasar el mouse sobre una palabra.")
            self.dict_note.setVisible(not self.dictionary.available)
        else:
            if not ja_tokenizer.available:
                self.dict_note.setText("⚠ Falta SudachiPy: ejecuta «pip install sudachipy sudachidict_core» "
                                       "para ver significados al pasar el mouse.")
            else:
                self.dict_note.setText("⚠ Diccionario japonés no encontrado: ejecuta build_dictionary_ja.py "
                                       "para ver significados y lecturas al pasar el mouse.")
            self.dict_note.setVisible(not (ja_tokenizer.available and self.dictionary_ja.available))

    def on_language_changed(self):
        code = self.combo_lang.currentData()
        languages.set_active(code)
        self.settings.setValue("language", code)
        self._apply_language_ui()
        self.words.hide_popup()
        self.output.clear()
        self.translation.clear()
        self._last_image = None
        self.btn_capture.setEnabled(False)
        self.status.setText("Cambiando de idioma...")
        self.refresh_marks()
        self.configure_requested.emit(code, self.combo_mode.currentData())

    def on_mode_changed(self):
        mode = self.combo_mode.currentData()
        self.settings.setValue("ocr_mode", mode)
        self.configure_requested.emit(self.combo_lang.currentData(), mode)

    # --- palabras guardadas ---
    def refresh_marks(self):
        """Resalta las palabras guardadas en el texto original de esta ventana y en la capa."""
        self.output.set_marks(self.words.marks(self.output.toPlainText()))
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.set_marks(self.words.marks(self.overlay.text))
        if self.log_window is not None:
            self.log_window.refresh_marks()

    def _update_buttons(self):
        self.btn_cards.setText(f"Palabras guardadas ({len(self.store)})")
        self.btn_log.setText(f"Registro de frases ({len(self.log)})")

    def on_word_clicked(self, word, compound, start):
        self.words.click(word, compound, self.output.toPlainText(), start, self._merge_used)

    def on_overlay_word_clicked(self, word, compound, start):
        self.words.click(word, compound, self.overlay.text, start, self._merge_used)

    def _on_word_message(self, text):
        self.status.setText(text)
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.show_toast(text)
        if self.log_window is not None and self.log_window.isVisible():
            self.log_window.show_message(text)

    def on_card_translated(self, card_id, sentence_es, word_es):
        if self.words.card_translated(card_id, sentence_es, word_es) and self.cards_window is not None:
            self.cards_window.reload()

    def _cards_changed(self):
        """Tras guardar o borrar: actualiza resaltado, contador y lista."""
        self.refresh_marks()
        self._update_buttons()
        if self.cards_window is not None:
            self.cards_window.reload()

    def open_cards(self):
        if self.cards_window is None:
            self.cards_window = CardsWindow(self.store, self)
            self.cards_window.changed.connect(self._cards_changed)
        self.cards_window.reload()
        self.cards_window.show()
        self.cards_window.raise_()
        self.cards_window.activateWindow()

    def open_log(self):
        if self.log_window is None:
            self.log_window = LogWindow(self.log, self.words, self)
        self.log_window.reload()
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()

    # --- flujo de captura ---
    def _minimize_windows(self) -> bool:
        """Minimiza las ventanas de la app para que no salgan en la captura (siguen en la barra
        de tareas y no se vuelven a abrir solas). ¿Alguna estaba a la vista?"""
        shown = False
        for w in (self, self.cards_window, self.log_window):
            if w is not None and w.isVisible() and not w.isMinimized():
                shown = True
                w.showMinimized()
        return shown

    def start_capture(self):
        if not self.btn_capture.isEnabled() or self._grabbing:
            return
        self._grabbing = True
        self.words.hide_popup()
        was_visible = self._minimize_windows()
        if self.overlay is not None and self.overlay.isVisible():   # nueva captura con la capa abierta
            self.overlay.hide()
            was_visible = True
        QTimer.singleShot(300 if was_visible else 0, self._grab_and_open)

    def _grab_and_open(self):
        try:
            frame = grab_screen()
        finally:
            self._grabbing = False
        if self.overlay is None:
            self.overlay = CaptureOverlay()
            self.overlay.region_selected.connect(self.on_region)
            self.overlay.word_hovered.connect(self.words.hover)
            self.overlay.hover_ended.connect(self.words.hover_left)
            self.overlay.wheel_moved.connect(self.words.scroll_popup)
            self.overlay.word_clicked.connect(self.on_overlay_word_clicked)
            self.overlay.merge_toggled.connect(self.on_merge_toggled)
            self.overlay.closed.connect(self.on_overlay_closed)
        self.overlay.open(frame)

    def on_overlay_closed(self):
        self.words.hide_popup()
        self.status.setText(f"Listo. {self._ocr_info}" if self._ocr_info else "Listo.")

    def on_region(self, image):
        pix = bgr_to_qpixmap(image)
        self.preview.setPixmap(pix.scaledToWidth(min(pix.width(), 480), Qt.SmoothTransformation))
        self._request(image)

    def on_merge_toggled(self):
        """Tecla M en la capa: cambia «unir líneas» y vuelve a procesar la misma región."""
        if self._last_image is None or not self.btn_capture.isEnabled():
            return
        self.chk_merge.setChecked(not self.chk_merge.isChecked())
        self._request(self._last_image)
        self.overlay.show_toast("Líneas unidas" if self.chk_merge.isChecked() else "Líneas separadas")

    def _request(self, image):
        self._last_image = image
        self.translation.clear()
        self.status.setText("Procesando...")
        self.btn_capture.setEnabled(False)
        self._merge_used = self.chk_merge.isChecked()
        self.process_requested.emit(image, self._merge_used)

    # --- resultados del worker ---
    def on_ready(self):
        self.status.setText(f"Listo. Pulsa el botón o {HOTKEY_LABEL}.")
        self.btn_capture.setEnabled(True)

    def on_result(self, res):
        self.output.setPlainText(res.text)   # vacío si no se detectó nada (se resalta al cambiar el texto)
        self._log_id = None
        if res.lines:
            avg = sum(s for _, s in res.lines) / len(res.lines)
            self._ocr_info = f"{len(res.lines)} línea(s), confianza media {avg:.0%}"
            self.status.setText(f"OCR: {self._ocr_info}. Traduciendo...")
            if res.text.strip():
                self._log_id = self.log.add(res.text, "", self._merge_used, avg)
                self._log_changed()
        else:
            self._ocr_info = ""
            self.status.setText("Sin texto detectado.")
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.set_result(res)
            self.overlay.set_marks(self.words.marks(self.overlay.text))

    def on_translated(self, text):
        self.translation.setPlainText(text)
        self.status.setText(f"Listo. {self._ocr_info}")
        if self._log_id is not None:
            self.log.set_translation(self._log_id, text)
            self._log_changed()
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.set_translation(text)

    def _log_changed(self):
        self._update_buttons()
        if self.log_window is not None:
            self.log_window.reload()

    def on_done(self):
        self.btn_capture.setEnabled(True)

    def on_failed(self, msg):
        self.status.setText(msg)
        self.btn_capture.setEnabled(self.worker._engine is not None)
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.set_message(msg)

    def copy(self, box):
        QGuiApplication.clipboard().setText(box.toPlainText())

    # --- cierre ---
    def closeEvent(self, event):
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self):
        self.words.hide_popup()
        if self.overlay is not None:
            self.overlay.hide()
        for w in (self.cards_window, self.log_window):
            if w is not None:
                w.close()
        self.hotkey.stop()
        self.thread.quit()
        self.thread.wait(3000)
        self.dictionary.close()
        self.dictionary_ja.close()
        self.store.close()
        self.log.close()
        QApplication.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
