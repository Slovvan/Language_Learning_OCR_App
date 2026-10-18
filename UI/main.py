"""Fase 4: captura + OCR + traducción EN -> ES + diccionario + guardar palabras para Anki."""
# IMPORTANTE (Windows): las librerías nativas se importan ANTES que PyQt5.
# Importarlas por primera vez dentro de un hilo con Qt ya cargado provoca
# "access violation".
import onnxruntime  # noqa: F401
import ctranslate2  # noqa: F401

import sys

from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel,
                             QPushButton, QTextEdit, QVBoxLayout, QWidget)

from UI.capture import RegionSelector, bgr_to_qpixmap, grab_screen
from Cards.cards import CardStore, build_card
from UI.cards_window import CardsWindow
from DB.dictionary import Dictionary
from Cards.sentences import sentence_at
from UI.word_popup import HoverTextEdit, WordPopup, iter_words
from UI.ocr_worker import OcrWorker

HOTKEY = "<ctrl>+<alt>+o"


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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR para juegos - Fase 4 (guardar palabras para Anki)")
        self.resize(560, 700)
        self.selector = None
        self.cards_window = None
        self._ocr_info = ""
        self._merge_used = True     # ¿se unieron las líneas en la captura actual?

        # --- diccionario local, tarjetas guardadas y ventanita emergente ---
        self.dictionary = Dictionary()
        self.store = CardStore()
        self.popup = WordPopup()

        # --- UI ---
        self.btn_capture = QPushButton(f"Capturar región (ctrl+alt+O)")
        self.btn_capture.setEnabled(False)
        self.btn_cards = QPushButton()
        self.btn_copy_src = QPushButton("Copiar original")
        self.btn_copy_dst = QPushButton("Copiar traducción")
        self.chk_merge = QCheckBox("Unir líneas partidas (diálogos)")
        self.chk_merge.setChecked(True)
        self.chk_merge.setToolTip(
            "Actívalo para cuadros de diálogo donde una frase ocupa varias líneas.\n"
            "Desactívalo para menús o listas donde cada línea es independiente."
        )
        self.status = QLabel("Iniciando...")
        self.status.setWordWrap(True)
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
        self.output.setPlaceholderText("Texto original (OCR)...")
        self.translation = QTextEdit()
        self.translation.setPlaceholderText("Traducción al español...")

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_capture)
        buttons.addWidget(self.btn_cards)
        buttons2 = QHBoxLayout()
        buttons2.addWidget(self.btn_copy_src)
        buttons2.addWidget(self.btn_copy_dst)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addLayout(buttons2)
        layout.addWidget(self.chk_merge)
        layout.addWidget(self.status)
        layout.addWidget(self.dict_note)
        layout.addWidget(self.preview)
        layout.addWidget(QLabel("Original (inglés) — mouse sobre una palabra: significado · clic: guardarla"))
        layout.addWidget(self.output, 1)
        layout.addWidget(QLabel("Traducción (español)"))
        layout.addWidget(self.translation, 1)

        # --- hilo del OCR + traductor (los modelos se cargan una vez, dentro del hilo) ---
        self.thread = QThread()
        self.worker = OcrWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.load)
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
        self.btn_capture.clicked.connect(self.start_capture)
        self.btn_cards.clicked.connect(self.open_cards)
        self.btn_copy_src.clicked.connect(lambda: self.copy(self.output))
        self.btn_copy_dst.clicked.connect(lambda: self.copy(self.translation))
        self.output.word_hovered.connect(self.on_word_hovered)
        self.output.hover_ended.connect(self.popup.hide)
        self.output.word_clicked.connect(self.on_word_clicked)
        self.output.textChanged.connect(self.refresh_marks)

        # --- atajo global (opcional) ---
        self.hotkey = HotkeyBridge()
        self.hotkey.triggered.connect(self.start_capture)

        self._update_cards_button()

    # --- palabras guardadas ---
    def _is_saved(self, word, compound=""):
        return self.dictionary.key_for(word, compound) in self.store.keys

    def refresh_marks(self):
        """Resalta en el texto original las palabras que ya están guardadas."""
        ranges = []
        if len(self.store):
            for start, end, word, compound in iter_words(self.output.toPlainText()):
                if self._is_saved(word, compound):
                    ranges.append((start, end))
        self.output.set_marks(ranges)

    def _update_cards_button(self):
        self.btn_cards.setText(f"Palabras guardadas ({len(self.store)})")

    def on_word_clicked(self, word, compound, start):
        """Clic sobre una palabra: la guarda con su significado y su frase (una vez por lema)."""
        key = self.dictionary.key_for(word, compound)
        if self.store.has(key):
            self.status.setText(f"«{key}» ya estaba guardada.")
            return
        sentence = sentence_at(self.output.toPlainText(), start, self._merge_used) or ""
        result = self.dictionary.lookup(word, compound)
        card = self.store.add(build_card(word, key, result, sentence))
        # Sin entrada en el diccionario (p. ej. «mana»): se pide también la traducción de la palabra.
        self.card_requested.emit(card.id, sentence, "" if result is not None else word)
        self.status.setText(f"✓ «{card.word}» guardada ({len(self.store)} en la lista).")
        self._cards_changed()

    def on_card_translated(self, card_id, sentence_es, word_es):
        """Llega la traducción de la frase (y, si hacía falta, de la palabra) de una tarjeta."""
        card = self.store.get(card_id)
        if card is None:                    # se borró mientras se traducía
            return
        changes = {}
        if sentence_es and not card.sentence_es:
            changes["sentence_es"] = sentence_es
        if word_es and not card.meaning:
            changes["meaning"] = f"{word_es} (traducción automática)"
        if changes:
            self.store.update(card_id, **changes)
            if self.cards_window is not None:
                self.cards_window.reload()

    def _cards_changed(self):
        """Tras guardar o borrar: actualiza resaltado, contador y lista."""
        self.refresh_marks()
        self._update_cards_button()
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

    # --- significado de la palabra bajo el mouse ---
    def on_word_hovered(self, word, compound, global_pos):
        if not self.dictionary.available:
            return  # el aviso ya está visible en la ventana
        saved = self._is_saved(word, compound)
        result = self.dictionary.lookup(word, compound)
        if result is None:
            self.popup.show_message(f"«{word}»: sin entrada en el diccionario", global_pos, saved)
        else:
            self.popup.show_result(result, global_pos, saved)

    # --- flujo de captura ---
    def start_capture(self):
        if not self.btn_capture.isEnabled() or self.selector is not None:
            return
        self.popup.hide()
        self.hide()  # que la propia ventana no salga en la captura
        QTimer.singleShot(200, self._grab_and_select)

    def _grab_and_select(self):
        frame = grab_screen()
        self.selector = RegionSelector(frame)
        self.selector.selected.connect(self.on_region)
        self.selector.cancelled.connect(self.on_cancel)
        self.selector.start()

    def _close_selector(self):
        if self.selector is not None:
            self.selector.deleteLater()
            self.selector = None

    def on_region(self, image):
        self._close_selector()
        self.show()
        self.activateWindow()
        pix = bgr_to_qpixmap(image)
        self.preview.setPixmap(pix.scaledToWidth(min(pix.width(), 480), Qt.SmoothTransformation))
        self.translation.clear()
        self.status.setText("Procesando...")
        self.btn_capture.setEnabled(False)
        self._merge_used = self.chk_merge.isChecked()
        self.process_requested.emit(image, self._merge_used)

    def on_cancel(self):
        self._close_selector()
        self.show()
        self.status.setText("Captura cancelada.")

    # --- resultados del worker ---
    def on_ready(self):
        self.status.setText("Listo. Pulsa el botón o usa el atajo.")
        self.btn_capture.setEnabled(True)

    def on_result(self, res):
        self.output.setPlainText(res.text)   # vacío si no se detectó nada (se resalta al cambiar el texto)
        if res.lines:
            avg = sum(s for _, s in res.lines) / len(res.lines)
            self._ocr_info = f"{len(res.lines)} línea(s), confianza media {avg:.0%}"
            self.status.setText(f"OCR: {self._ocr_info}. Traduciendo...")
        else:
            self._ocr_info = ""
            self.status.setText("Sin texto detectado.")

    def on_translated(self, text):
        self.translation.setPlainText(text)
        self.status.setText(f"Listo. {self._ocr_info}")

    def on_done(self):
        self.btn_capture.setEnabled(True)

    def on_failed(self, msg):
        self.status.setText(msg)
        self.btn_capture.setEnabled(self.worker._engine is not None)

    def copy(self, box):
        QGuiApplication.clipboard().setText(box.toPlainText())

    def closeEvent(self, event):
        self.popup.hide()
        self.hotkey.stop()
        self.thread.quit()
        self.thread.wait(3000)
        self.dictionary.close()
        self.store.close()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())