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
from UI.live_mode import LiveMode
from UI.app_settings import get_ocr_bar, hotkey_label, load_bindings, load_keys
from UI.hotkeys import HotkeyManager
from UI.live_mode import LiveMode
from UI.settings_window import SettingsDialog
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
        self.settings_dialog = None
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
        self.btn_capture = QPushButton("Capturar región")
        self.btn_capture.setEnabled(False)
        self.combo_capture = QComboBox()
        self.combo_capture.addItem("Congelar la pantalla", "frozen")
        self.combo_capture.addItem("En vivo (región fija)", "live")
        self.btn_live_read = QPushButton("Leer región")
        self.btn_live_text = QPushButton("Texto (O)")
        self.btn_live_panel = QPushButton("Panel (T)")
        self.chk_live_auto = QCheckBox("Automático (A)")
        self.chk_freeze_read = QCheckBox("Congelar al leer")
        self.chk_freeze_read.setToolTip("Tras «Leer región», la región se queda congelada con el texto encima (F la descongela).\n"
                                        "Ojo: el juego sigue avanzando por detrás; solo la imagen se queda quieta.")
        self.chk_freeze_auto = QCheckBox("Congelar en automático")
        self.chk_freeze_auto.setToolTip("Cada frase detectada por la lectura automática congela la región (F la descongela).")
        for chk, key in ((self.chk_freeze_read, "live_freeze_read"), (self.chk_freeze_auto, "live_freeze_auto")):
            chk.setChecked(self.settings.value(key, False) in (True, "true", "1", 1))
        self.chk_live_orig = QCheckBox("Original en el panel")
        self.chk_live_orig.setToolTip("El panel muestra, además de la traducción, el texto reconocido.")
        self.chk_live_hist = QCheckBox("Historial")
        self.chk_live_hist.setToolTip("El panel acumula las últimas lecturas (la más nueva arriba), cada una con ✕ para borrarla.\n"
                                      "«Limpiar» en el panel las borra todas. El registro principal de la app no se toca.")
        self.chk_live_save = QCheckBox("Guardar en el registro")
        self.chk_live_save.setToolTip("Cada lectura del modo en vivo se guarda en «Registro de frases». Desmarcada, solo vive en el panel.")
        self.chk_live_over = QCheckBox("Traducción sobre la región")
        self.chk_live_over.setToolTip("Sobre la región se dibuja la traducción en lugar del texto reconocido.\n"
                                      "Mientras esté activa no se pueden guardar palabras con el mouse.")
        self.chk_live_temp = QCheckBox("Panel temporal")
        self.chk_live_temp.setToolTip("El panel aparece al leer y se esconde solo unos segundos después (si el mouse no está encima).")
        for chk, key, default in ((self.chk_live_orig, "live_show_original", False), (self.chk_live_hist, "live_history", False),
                                  (self.chk_live_save, "live_save_log", True), (self.chk_live_over, "live_over", False),
                                  (self.chk_live_temp, "live_panel_temp", False)):
            chk.setChecked(self.settings.value(key, default) in (True, "true", "1", 1))
        self.btn_live_edit = QPushButton("Región (E)")
        self.btn_live_stop = QPushButton("Detener")
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

        modes = QHBoxLayout()
        modes.addWidget(QLabel("Al capturar:"))
        modes.addWidget(self.combo_capture, 1)
        live_row = QHBoxLayout()
        for b in (self.btn_live_read, self.btn_live_text, self.btn_live_panel, self.btn_live_edit, self.btn_live_stop):
            live_row.addWidget(b)
        live_row2 = QHBoxLayout()
        live_row2.addWidget(self.chk_live_auto)
        live_row2.addWidget(self.chk_freeze_read)
        live_row2.addWidget(self.chk_freeze_auto)
        live_row2.addStretch(1)
        live_row3 = QHBoxLayout()
        for chk in (self.chk_live_orig, self.chk_live_hist, self.chk_live_save):
            live_row3.addWidget(chk)
        live_row3.addStretch(1)
        live_row4 = QHBoxLayout()
        for chk in (self.chk_live_over, self.chk_live_temp):
            live_row4.addWidget(chk)
        live_row4.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_capture)
        buttons2 = QHBoxLayout()
        buttons2.addWidget(self.btn_cards)
        buttons2.addWidget(self.btn_log)
        buttons2.addWidget(self.btn_glossary)
        self.btn_settings = QPushButton("Configuración…")
        buttons4 = QHBoxLayout()
        buttons4.addWidget(self.btn_settings)
        buttons3 = QHBoxLayout()
        buttons3.addWidget(self.btn_copy_src)
        buttons3.addWidget(self.btn_copy_dst)
        layout = QVBoxLayout(self)
        langs = QHBoxLayout()
        langs.addWidget(QLabel("Idioma del juego:"))
        langs.addWidget(self.combo_lang, 1)
        langs.addWidget(self.combo_mode, 1)
        layout.addLayout(langs)
        layout.addLayout(modes)
        layout.addLayout(live_row)
        layout.addLayout(live_row2)
        layout.addLayout(live_row3)
        layout.addLayout(live_row4)
        layout.addLayout(buttons)
        layout.addLayout(buttons2)
        layout.addLayout(buttons3)
        layout.addLayout(buttons4)
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
        self.combo_capture.currentIndexChanged.connect(self.on_capture_mode_changed)
        self.btn_live_read.clicked.connect(lambda: self.live.read_now())
        self.btn_live_text.clicked.connect(lambda: self.live.toggle_text())
        self.btn_live_panel.clicked.connect(lambda: self.live.toggle_panel())
        self.btn_live_edit.clicked.connect(lambda: self.live.toggle_edit())
        self.chk_live_auto.clicked.connect(lambda on: self.live.set_auto(on))
        for chk, key, arg in ((self.chk_live_orig, "live_show_original", "original"), (self.chk_live_hist, "live_history", "history"),
                              (self.chk_live_save, "live_save_log", "save_log"), (self.chk_live_over, "live_over", "over"),
                              (self.chk_live_temp, "live_panel_temp", "temp")):
            chk.toggled.connect(lambda on, key=key, arg=arg: (self.settings.setValue(key, on), self.live.set_panel_options(**{arg: on})))
        self.chk_freeze_read.toggled.connect(lambda on: (self.settings.setValue("live_freeze_read", on), self.live.set_freeze_options(on, None)))
        self.chk_freeze_auto.toggled.connect(lambda on: (self.settings.setValue("live_freeze_auto", on), self.live.set_freeze_options(None, on)))
        self.btn_live_stop.clicked.connect(lambda: self.live.stop())
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

        # --- atajo global (opcional) ---
        self.hotkey = HotkeyManager()
        self.hotkey.triggered.connect(self.on_hotkey)
        if not self.hotkey.available:
            print(f"[aviso] Atajo global no disponible: {self.hotkey.error}")
        self.hotkey.set_bindings(load_bindings(self.settings))

        # --- modo en vivo ---
        self.live = LiveMode(self)
        self._apply_hotkey_labels()
        self.live.active_changed.connect(lambda _a: self._update_capture_ui())
        self.live.message.connect(self.status.setText)
        self.live.select_requested.connect(self.start_capture)
        self.live.auto_changed.connect(self._sync_auto_check)
        self._sync_auto_check(self.live.auto)
        saved = str(self.settings.value("capture_mode", "frozen"))
        self.combo_capture.setCurrentIndex(max(0, self.combo_capture.findData(saved)))

        self._apply_language_ui()
        self._update_buttons()
        self._update_capture_ui()

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
        if hasattr(self, "live"):
            self._sync_mode_bars()
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
        self._sync_mode_bars()

    def set_ocr_mode(self, mode: str):
        """Cambia el modo de OCR del japonés (barra, teclas 1/2/3 o el selector) y vuelve a leer lo que haya en pantalla."""
        if not languages.get_pack(self.combo_lang.currentData()).has_ocr_modes:
            return
        idx = self.combo_mode.findData(mode)
        if idx < 0:
            return
        if idx != self.combo_mode.currentIndex():
            self.combo_mode.setCurrentIndex(idx)             # on_mode_changed guarda y avisa al worker
        else:
            self._sync_mode_bars()
        names = {"normal": "OCR normal", "kana": "OCR solo kana", "vertical": "OCR vertical"}
        if self.overlay is not None and self.overlay.isVisible() and self.overlay.can_reprocess \
                and self._last_image is not None and self.btn_capture.isEnabled():
            self._request(self._last_image)
            self.overlay.show_toast(names.get(mode, mode))
        elif self.live.active:
            self.live.reread()

    def _sync_mode_bars(self):
        """Las barras de modos (captura congelada y panel en vivo) muestran el modo actual, solo con japonés."""
        has = languages.get_pack(self.combo_lang.currentData()).has_ocr_modes
        policy, mode = get_ocr_bar(self.settings), str(self.combo_mode.currentData())
        for bar in (self.live.panel.mode_bar,) + ((self.overlay.mode_bar,) if self.overlay is not None else ()):
            bar.set_policy(policy)
            bar.set_available(has)
            bar.set_mode(mode)

    # --- palabras guardadas ---
    def refresh_marks(self):
        """Resalta las palabras guardadas en el texto original de esta ventana y en la capa."""
        self.output.set_marks(self.words.marks(self.output.toPlainText()),
                              self.words.pending_marks(self.output.toPlainText()))
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.set_marks(self.words.marks(self.overlay.text), self.words.pending_marks(self.overlay.text))
        if hasattr(self, "live"):
            self.live.refresh_marks()
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

    # --- modo en vivo ---
    def _live_selected(self) -> bool:
        return self.combo_capture.currentData() == "live"

    def on_capture_mode_changed(self):
        if self.live.active:                                   # con el vivo en marcha no se cambia de modo
            self.combo_capture.blockSignals(True)
            self.combo_capture.setCurrentIndex(self.combo_capture.findData("live"))
            self.combo_capture.blockSignals(False)
            return
        self.settings.setValue("capture_mode", self.combo_capture.currentData())
        self._update_capture_ui()

    def open_settings(self):
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.settings, self)
            self.settings_dialog.changed.connect(self.apply_settings)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def apply_settings(self):
        """Atajos, textos con su atajo y valores numéricos del modo en vivo."""
        self.hotkey.set_bindings(load_bindings(self.settings))
        self.live.apply_settings()
        if self.overlay is not None:
            self.overlay.set_keys(load_keys(self.settings, "frozen"))
        self._sync_mode_bars()
        self._apply_hotkey_labels()
        self._update_capture_ui()

    def _apply_hotkey_labels(self):
        k = lambda a: hotkey_label(self.settings, a)          # noqa: E731
        self.btn_live_read.setText(f"Leer región ({k('read')})")
        self.btn_live_text.setToolTip(f"Muestra u oculta el texto dibujado sobre el original ({k('text')})")
        self.btn_live_panel.setToolTip(f"Muestra u oculta el panel con la traducción ({k('panel')})")
        self.btn_live_edit.setToolTip(f"Mueve o cambia el tamaño de la región fija ({k('edit')})")
        self.chk_live_auto.setToolTip(f"Lee solo cuando el texto de la región cambia y se queda quieto ({k('auto')}).\n"
                                      "Si lo desmarcas, solo se lee con «Leer región».")
        self.chk_freeze_read.setToolTip(f"Tras «Leer región», la pantalla se queda congelada con el texto encima ({k('freeze')} la descongela).\n"
                                        "Ojo: el juego sigue avanzando por detrás; solo la imagen se queda quieta.")
        self.chk_freeze_auto.setToolTip(f"Cada frase detectada por la lectura automática congela la pantalla ({k('freeze')} la descongela).")
        self.live.panel.btn_auto.setToolTip(f"Lee solo cuando el texto de la región cambia y se queda quieto ({k('auto')})")
        self.live.panel.btn_close.setToolTip(f"Cierra el panel (T o {k('panel')} lo vuelve a abrir)")

    def _update_capture_ui(self):
        live = self._live_selected()
        self.combo_capture.setEnabled(not self.live.active)
        self.combo_capture.setToolTip("Detén el modo en vivo para cambiar de modo." if self.live.active
                                      else "Congelar: eliges una región en cada captura.\n"
                                           "En vivo: eliges la región una vez y la lees cuando quieras.")
        self.btn_capture.setText(f"{'Elegir región fija' if live else 'Capturar región'} ({hotkey_label(self.settings, 'capture')})")
        self.chk_live_auto.setVisible(live)
        self.chk_freeze_read.setVisible(live)
        self.chk_freeze_auto.setVisible(live)
        for chk in (self.chk_live_orig, self.chk_live_hist, self.chk_live_save, self.chk_live_over, self.chk_live_temp):
            chk.setVisible(live)
        for b in (self.btn_live_read, self.btn_live_text, self.btn_live_panel, self.btn_live_edit, self.btn_live_stop):
            b.setVisible(live)
            b.setEnabled(self.live.active)
        self.how_to.setText(
            f"Modo en vivo: elige la región una vez; con «Leer» ({hotkey_label(self.settings, 'read')}) se lee y el texto aparece sobre el original; "
            "con el mouse ves y guardas palabras. Como en la captura congelada: T panel, O texto, M unir líneas, E mover o "
            "estirar la región, F congelar/descongelar, Esc detiene y clic derecho elige otra región (haz clic en la región o el panel para que las "
            "teclas funcionen; desde el juego usa los atajos de Configuración)."
            if live else
            "Tras elegir la región, la pantalla queda congelada con el texto encima. "
            "Esc la cierra. Esta ventana se minimiza al capturar y no vuelve a aparecer sola: "
            "cámbiate a ella desde la barra de tareas cuando quieras ver el registro.")

    def _sync_auto_check(self, on):
        self.chk_live_auto.blockSignals(True)
        self.chk_live_auto.setChecked(bool(on))
        self.chk_live_auto.blockSignals(False)

    def on_hotkey(self, action):
        if action == "capture":
            self.start_capture()
        elif action == "read":
            self.live.read_now()
        elif action == "text":
            self.live.toggle_text()
        elif action == "panel":
            self.live.toggle_panel()
        elif action == "merge":
            self.live.toggle_merge()
        elif action == "edit":
            self.live.toggle_edit()
        elif action == "auto":
            self.live.toggle_auto()
        elif action == "freeze":
            self.live.toggle_freeze()

    def on_live_region(self, box, rect):
        self.live.start(box, rect)

    def request_live(self, image):
        """Lectura pedida por el modo en vivo."""
        pix = bgr_to_qpixmap(image)
        self.preview.setPixmap(pix.scaledToWidth(min(pix.width(), 480), Qt.SmoothTransformation))
        self._request(image)

    def start_capture(self):
        if not self.btn_capture.isEnabled() or self._grabbing:
            return
        self._grabbing = True
        self.words.hide_popup()
        self.live.hide_for_select()                      # la región y el panel no deben salir en la captura congelada
        was_visible = self._minimize_windows() or self.live.active
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
            self.overlay.set_keys(load_keys(self.settings, "frozen"))
            self.overlay.region_selected.connect(self.on_region)
            self.overlay.word_anchor.connect(self.popup.set_anchor)
            self.overlay.hover_ended.connect(lambda: self.popup.set_anchor(None))
            self.overlay.word_context.connect(self.words.set_context)
            self.overlay.word_hovered.connect(self.words.hover)
            self.overlay.hover_ended.connect(self.words.hover_left)
            self.overlay.wheel_moved.connect(self.words.scroll_popup)
            self.overlay.word_clicked.connect(self.on_overlay_word_clicked)
            self.overlay.merge_toggled.connect(self.on_merge_toggled)
            self.overlay.mode_requested.connect(self.set_ocr_mode)
            self.overlay.closed.connect(self.on_overlay_closed)
            self.overlay.region_chosen.connect(self.on_live_region)
        self._sync_mode_bars()
        self.overlay.open(frame, select_only=self._live_selected())

    def on_overlay_closed(self):
        self.words.hide_popup()
        self.live.restore_after_select()
        if not self.live.active:
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
        self.status.setText(f"Listo. Pulsa el botón o {hotkey_label(self.settings, 'capture')}.")
        self.btn_capture.setEnabled(True)

    def on_result(self, res):
        if self.live.inflight and not self.live.accept_result(res):
            self.status.setText("En vivo: sin cambios en el texto." if res.text.strip() else "En vivo: sin texto.")
            return                                           # lectura automática repetida o vacía: se conserva todo
        self.output.setPlainText(res.text)   # vacío si no se detectó nada (se resalta al cambiar el texto)
        self._log_id = None
        if res.lines:
            avg = sum(s for _, s in res.lines) / len(res.lines)
            self._ocr_info = f"{len(res.lines)} línea(s), confianza media {avg:.0%}"
            self.status.setText(f"OCR: {self._ocr_info}. Traduciendo...")
            if res.text.strip() and (not self.live.inflight or self.live.save_log):
                self._log_id = self.log.add(res.text, "", self._merge_used, avg)
                self._log_changed()
        else:
            self._ocr_info = ""
            self.status.setText("Sin texto detectado.")
        if self.overlay is not None and self.overlay.isVisible():
            self.overlay.set_result(res)
            self.overlay.set_marks(self.words.marks(self.overlay.text), self.words.pending_marks(self.overlay.text))
        self.live.show_result(res)

    def on_translated(self, text):
        if self.live.skip:
            return
        self.live.show_translation(text)
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
        self.live.finished()

    def on_failed(self, msg):
        self.status.setText(msg)
        self.live.failed(msg)
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
        self.live.shutdown()
        for w in (self.cards_window, self.log_window, self.glossary_window):
            if w is not None:
                w.close()
        self.hotkey.stop()
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
