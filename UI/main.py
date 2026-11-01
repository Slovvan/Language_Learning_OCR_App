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


from DB.dictionary import Dictionary
from DB.dictionary_ja import JaDictionary
from DB.dictionary_fr import FrDictionary
from DB.ja_nlp import tokenizer as ja_tokenizer
from DB.phrase_log import PhraseLog
from DB.glossary import GlossaryDictionary, UserGlossary
from UI.glossary_window import GlossaryWindow
from UI.log_view import LogWindow
from UI.overlay import SELECT, CaptureOverlay
from UI.word_popup import HoverTextEdit, WordPopup
from UI.word_service import WordService
from UI.ocr_worker import OcrWorker
from UI.capture import bgr_to_qpixmap, grab_screen
from UI.cards_window import CardsWindow
from UI.settings import AppSettings
from UI.settings_window import SettingsDialog
from UI.hotkeys import HotkeyManager, global_label
from Cards.cards import CardStore

import Translator.japanese as japanese
import Translator.languages as languages



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


def _has_simplemma() -> bool:
    try:
        import simplemma  # noqa: F401
        return True
    except ImportError:
        return False


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
        self.glossary_window = None
        self.log_window = None
        self._ocr_info = ""
        self._merge_used = True     # ¿se unieron las líneas en la captura actual?
        self._last_image = None     # recorte de la última captura (para repetir con otro «unir líneas»)
        self._log_id = None         # entrada del registro de la captura en curso
        self._grabbing = False

        # --- idioma del juego y modo de OCR (se recuerdan entre sesiones) ---
        self.settings = QSettings("OCRTranslator", "fase6")
        self.app_settings = AppSettings(self.settings)      # atajos, barra de la capa…
        self.settings_window = None
        lang = str(self.settings.value("language", "en"))
        if lang not in languages.PACKS:
            lang = "en"
        mode = str(self.settings.value("ocr_mode", "normal"))
        if mode not in ("normal", "kana", "vertical"):
            mode = "normal"
        languages.set_active(lang)

        # --- diccionario local, tarjetas, registro y ventanita emergente ---
        self.dictionary = Dictionary()
        self.dictionary_ja = JaDictionary()
        self.dictionary_fr = FrDictionary()
        self.store = CardStore(lang=lang)       # cada idioma tiene su lista de palabras...
        self.log = PhraseLog(lang=lang)         # ...y su registro de frases
        self.glossary = UserGlossary()          # palabras sin significado (pendientes) y las que rellenas tú
        self._dicts = {"en": GlossaryDictionary(self.dictionary, self.glossary, "en"),
                       "ja": GlossaryDictionary(self.dictionary_ja, self.glossary, "ja"),
                       "fr": GlossaryDictionary(self.dictionary_fr, self.glossary, "fr")}
        self.popup = WordPopup()
        self.words = WordService(self._dictionary_for(lang), self.store, self.popup, self, self.glossary)
        self.words.saved_changed.connect(self._cards_changed)
        self.words.message.connect(self._on_word_message)
        self.words.card_requested.connect(self.card_requested)

        # --- UI ---
        self.btn_capture = QPushButton(f"Capturar región ({self._hotkey_label()})")
        self.btn_capture.setEnabled(False)
        self.btn_settings = QPushButton("Configuración…")
        self.btn_settings.setToolTip("Atajos, teclas de la captura y comportamiento de la capa")
        self.btn_cards = QPushButton()
        self.btn_log = QPushButton()
        self.btn_glossary = QPushButton()
        self.btn_copy_src = QPushButton("Copiar original")
        self.btn_copy_dst = QPushButton("Copiar traducción")
        self.combo_lang = QComboBox()
        for code, pack in languages.PACKS.items():
            self.combo_lang.addItem(pack.label, code)
        self.combo_lang.setCurrentIndex(self.combo_lang.findData(lang))
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("OCR normal (con kanji)", "normal")
        self.combo_mode.addItem("OCR solo kana (todo hiragana/katakana)", "kana")
        self.combo_mode.addItem("OCR texto vertical (columnas, de derecha a izquierda)", "vertical")
        self.combo_mode.setCurrentIndex(self.combo_mode.findData(mode))
        self.combo_mode.setToolTip("«Solo kana» corrige más confusiones en juegos sin kanji. «Texto vertical» lee cada columna "
            "como una frase y las une de derecha a izquierda.")
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
        buttons.addWidget(self.btn_capture, 1)
        buttons.addWidget(self.btn_settings)
        buttons2 = QHBoxLayout()
        buttons2.addWidget(self.btn_cards)
        buttons2.addWidget(self.btn_log)
        buttons2.addWidget(self.btn_glossary)
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
        self.chk_merge.toggled.connect(lambda _c: self._sync_toolbar())
        self.btn_capture.clicked.connect(self.start_capture)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_cards.clicked.connect(self.open_cards)
        self.btn_log.clicked.connect(self.open_log)
        self.btn_glossary.clicked.connect(self.open_glossary)
        self.btn_copy_src.clicked.connect(lambda: self.copy(self.output))
        self.btn_copy_dst.clicked.connect(lambda: self.copy(self.translation))
        self.output.word_context.connect(self.words.set_context)
        self.output.word_hovered.connect(self.words.hover)
        self.output.hover_ended.connect(self.words.hover_left)
        self.output.wheel_moved.connect(self.words.scroll_popup)
        self.output.word_clicked.connect(self.on_word_clicked)
        self.output.textChanged.connect(self.refresh_marks)

        # --- atajos globales (opcional: necesitan pynput) ---
        self.hotkeys = HotkeyManager()
        self.hotkeys.triggered.connect(self.on_hotkey)
        self.hotkeys.set_bindings(self.app_settings.hotkeys())
        self.app_settings.changed.connect(self.on_setting_changed)

        self._apply_language_ui()
        self._update_buttons()

    # --- idioma ---
    def _dictionary_for(self, code):
        return self._dicts[code]

    def _apply_language_ui(self):
        pack = languages.active()
        self.words.dictionary = self._dictionary_for(pack.code)
        self.lbl_original.setText(
            f"Original ({pack.source_name}) — mouse sobre una palabra: significado · clic: guardarla")
        self.lbl_translation.setText(f"Traducción ({pack.target_name})")
        self.translation.setPlaceholderText(f"Traducción al {pack.target_name}...")
        self.combo_mode.setVisible(pack.has_ocr_modes)
        self._sync_toolbar()
        if pack.code == "en":
            self.dict_note.setText("⚠ Diccionario no encontrado: ejecuta build_dictionary.py para ver "
                                   "significados al pasar el mouse sobre una palabra.")
            self.dict_note.setVisible(not self.dictionary.available)
        elif pack.code == "fr":
            if not self.dictionary_fr.available:
                self.dict_note.setText("⚠ Diccionario francés no encontrado: ejecuta build_dictionary_fr.py "
                                       "para ver significados al pasar el mouse sobre una palabra.")
            else:
                self.dict_note.setText("⚠ Falta «simplemma» (pip install simplemma): sin él solo se reconocen "
                                       "las formas exactas (no «chevaux» → «cheval»).")
            self.dict_note.setVisible(not self.dictionary_fr.available or not _has_simplemma())
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
        self._log_id = None
        self.store.set_language(code)          # las palabras y el registro son los de ese idioma
        self.log.set_language(code)
        self._update_buttons()
        for w in (self.cards_window, self.log_window):
            if w is not None:
                w.reload()
        if self.glossary_window is not None:
            self.glossary_window.set_language(code)
        self.refresh_marks()
        self.configure_requested.emit(code, self.combo_mode.currentData())

    def on_mode_changed(self):
        mode = self.combo_mode.currentData()
        self.settings.setValue("ocr_mode", mode)
        self.configure_requested.emit(self.combo_lang.currentData(), mode)
        self._sync_toolbar()

    # --- palabras guardadas ---
    def refresh_marks(self):
        """Resalta las palabras guardadas en el texto original de esta ventana y en la capa."""
        self.output.set_marks(self.words.marks(self.output.toPlainText()),
                              self.words.pending_marks(self.output.toPlainText()))
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.set_marks(self.words.marks(self.overlay.text), self.words.pending_marks(self.overlay.text))
        if self.log_window is not None:
            self.log_window.refresh_marks()

    def _update_buttons(self):
        name = languages.active().source_name
        self.btn_cards.setText(f"Palabras guardadas — {name} ({len(self.store)})")
        self.btn_log.setText(f"Registro de frases — {name} ({len(self.log)})")
        lang = languages.active().code
        self.btn_glossary.setText(f"Pendientes — {name} ({self.glossary.count(lang, True)})")

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

    def open_glossary(self):
        lang = languages.active().code
        if self.glossary_window is None:
            self.glossary_window = GlossaryWindow(
                self.glossary, lambda word: self.words.dictionary.key_for(word, ""), self)
            self.glossary_window.changed.connect(self._cards_changed)
        self.glossary_window.set_language(lang)
        self.glossary_window.show()
        self.glossary_window.raise_()
        self.glossary_window.activateWindow()

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
        for w in (self, self.cards_window, self.log_window, self.glossary_window):
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
            self.overlay = CaptureOverlay(settings=self.app_settings)
            self.overlay.region_selected.connect(self.on_region)
            self.overlay.mode_requested.connect(self.on_overlay_mode)
            self.overlay.settings_requested.connect(self.open_settings)
            self.overlay.word_context.connect(self.words.set_context)
            self.overlay.word_hovered.connect(self.words.hover)
            self.overlay.hover_ended.connect(self.words.hover_left)
            self.overlay.wheel_moved.connect(self.words.scroll_popup)
            self.overlay.word_clicked.connect(self.on_overlay_word_clicked)
            self.overlay.merge_toggled.connect(self.on_merge_toggled)
            self.overlay.closed.connect(self.on_overlay_closed)
        self._sync_toolbar()
        self.overlay.open(frame)

    def on_overlay_closed(self):
        self.words.hide_popup()
        self.status.setText(f"Listo. {self._ocr_info}" if self._ocr_info else "Listo.")

    def on_region(self, image):
        pix = bgr_to_qpixmap(image)
        self.preview.setPixmap(pix.scaledToWidth(min(pix.width(), 480), Qt.SmoothTransformation))
        self._request(image)

    def _reading_overlay(self) -> bool:
        """¿La capa tiene ya una región para volver a procesar?"""
        return (self.overlay is not None and self.overlay.isVisible() and self.overlay.state != SELECT
                and self._last_image is not None)

    def on_merge_toggled(self):
        """Tecla M o botón «Unir líneas» de la capa: cambia la casilla y vuelve a procesar la misma región."""
        if self._reading_overlay() and not self.btn_capture.isEnabled():
            self._sync_toolbar()                         # procesando: se espera
            return
        self.chk_merge.setChecked(not self.chk_merge.isChecked())
        self._sync_toolbar()
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.show_toast("Líneas unidas" if self.chk_merge.isChecked() else "Líneas separadas")
        if self._reading_overlay():
            self._request(self._last_image)

    def on_overlay_mode(self, mode):
        """Teclas 1-2-3 o botones de la capa: cambia el modo de OCR (japonés) y relee la misma región."""
        if not languages.active().has_ocr_modes:
            return
        if self._reading_overlay() and not self.btn_capture.isEnabled():
            self._sync_toolbar()
            return
        index = self.combo_mode.findData(mode)
        if index < 0:
            return
        changed = index != self.combo_mode.currentIndex()
        self.combo_mode.setCurrentIndex(index)           # on_mode_changed avisa al worker
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.show_toast(f"Modo de OCR: {self.combo_mode.currentText().split(' (')[0]}")
        if changed and self._reading_overlay():
            self._request(self._last_image)

    def _sync_toolbar(self):
        if self.overlay is not None:
            self.overlay.set_toolbar_state(str(self.combo_mode.currentData()), self.chk_merge.isChecked(),
                                           languages.active().has_ocr_modes)

    # --- configuración y atajos ---
    def _hotkey_label(self) -> str:
        return global_label(self.app_settings.get("hotkey.capture"))

    def on_hotkey(self, action):
        if action == "capture":
            self.start_capture()

    def on_setting_changed(self, key):
        if key.startswith("hotkey."):
            self.hotkeys.set_bindings(self.app_settings.hotkeys())
            self.btn_capture.setText(f"Capturar región ({self._hotkey_label()})")
            if self.btn_capture.isEnabled():
                self.status.setText(f"Listo. Pulsa el botón o {self._hotkey_label()}.")

    def open_settings(self):
        if self.settings_window is None:
            self.settings_window = SettingsDialog(self.app_settings, self.hotkeys)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _request(self, image):
        self._last_image = image
        self.translation.clear()
        self.status.setText("Procesando...")
        self.btn_capture.setEnabled(False)
        self._merge_used = self.chk_merge.isChecked()
        self.process_requested.emit(image, self._merge_used)

    # --- resultados del worker ---
    def on_ready(self):
        self.status.setText(f"Listo. Pulsa el botón o {self._hotkey_label()}.")
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
            self.overlay.set_marks(self.words.marks(self.overlay.text), self.words.pending_marks(self.overlay.text))

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
        for w in (self.cards_window, self.log_window, self.glossary_window):
            if w is not None:
                w.close()
        self.hotkeys.stop()
        if self.settings_window is not None:
            self.settings_window.close()
        self.thread.quit()
        self.thread.wait(3000)
        self.dictionary.close()
        self.dictionary_ja.close()
        self.dictionary_fr.close()
        self.glossary.close()
        self.store.close()
        self.log.close()
        QApplication.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
