# IMPORTANTE (Windows): las librerías nativas se importan ANTES que PyQt5.
# Importarlas por primera vez dentro de un hilo con Qt ya cargado provoca
# "access violation".
import onnxruntime  # noqa: F401
import ctranslate2  # noqa: F401

import sys

from PyQt5.QtCore import QObject, QRect, QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

import UI.profiles as profiles
from DB.dictionary import Dictionary
from DB.dictionary_ja import JaDictionary
from DB.dictionary_fr import FrDictionary
from DB.ja_nlp import tokenizer as ja_tokenizer
from DB.db_safe import open_safe
from DB.phrase_log import PhraseLog
from DB.glossary import GlossaryDictionary, UserGlossary
from UI.glossary_window import GlossaryWindow
from UI.main_ui import NavButton, StatusLabel, confirm, build_qss, set_prop
from UI.log_view import LogPage
from UI.overlay import SELECT, CaptureOverlay
from UI.word_popup import HoverTextEdit, WordPopup
from UI.word_service import WordService
from UI.ocr_worker import OcrWorker
from UI.capture import bgr_to_qpixmap, grab_screen
from UI.cards_window import CardsPage
from UI.hotkeys import HotkeyManager, global_label
from UI.live_mode import LiveMode
from UI.app_settings import get_ocr_bar, hotkey_label, load_bindings, load_keys, get_number
from UI.hotkeys import HotkeyManager
from UI.live_mode import LiveMode
from UI.live_options import LiveOptions
from UI.settings_window import SettingsDialog
from Cards.cards import CardStore

import Cards.cards as _cards_mod
import DB.glossary as _glossary_mod
import DB.phrase_log as _log_mod

import Translator.japanese as japanese
import Translator.languages as languages
import UI.main_ui as main_ui


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
    glossary_requested = pyqtSignal(int, str)      # id de una palabra pendiente y su frase, para traducirla
    configure_requested = pyqtSignal(str, str)     # idioma del juego, modo de OCR

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR Translator")
        self.setWindowIcon(make_icon())
        self.resize(960, 680)
        self.setMinimumSize(840, 560)
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
        self._startup_notes = []                # avisos de arranque (p. ej. una base de datos dañada que se apartó)
        self.store = open_safe(lambda: CardStore(lang=lang), _cards_mod.DB_PATH, self._startup_notes)   # cada idioma tiene su lista de palabras...
        self.log = open_safe(lambda: PhraseLog(lang=lang), _log_mod.DB_PATH, self._startup_notes)  # ...y su registro de frases
        self.glossary = open_safe(UserGlossary, _glossary_mod.DB_PATH, self._startup_notes)               # palabras sin significado y las que rellenas tú
        self._dicts = {"en": GlossaryDictionary(self.dictionary, self.glossary, "en"),
                       "ja": GlossaryDictionary(self.dictionary_ja, self.glossary, "ja"),
                       "fr": GlossaryDictionary(self.dictionary_fr, self.glossary, "fr")}
        self.popup = WordPopup()
        self.popup.set_scale(get_number(self.settings, "popup_scale"))
        self.words = WordService(self._dictionary_for(lang), self.store, self.popup, self, self.glossary)
        self.words.merge_getter = lambda: self._merge_used
        self.words.saved_changed.connect(self._cards_changed)
        self.words.message.connect(self._on_word_message)
        self.words.card_requested.connect(self.card_requested)
        self.words.glossary_requested.connect(self.glossary_requested)

        # --- UI ---
        self.btn_capture = QPushButton("Capturar región")
        self.btn_capture.setEnabled(False)
        self.combo_capture = QComboBox()
        self.combo_capture.addItem("Congelar la pantalla", "frozen")
        self.combo_capture.addItem("En vivo (región fija)", "live")
        self.btn_live_read = QPushButton("Leer región")
        self.btn_live_text = QPushButton("Texto (O)")
        self.btn_live_panel = QPushButton("Panel (T)")
        self.profile_label = QLabel("Perfil activo")
        self.combo_profile = QComboBox()                  # perfiles por juego (modo en vivo)
        self.combo_profile.setToolTip("Perfil del juego: región fija, idioma, modo de OCR y opciones del modo en vivo.")
        self.btn_profile_save = QPushButton("Guardar…")
        self.btn_profile_save.setToolTip("Guarda la región, el idioma, el modo de OCR y las opciones actuales como perfil de este juego.")
        self.btn_live_edit = QPushButton("Región (E)")
        self.btn_live_stop = QPushButton("Detener")
        self.btn_nav_capture = NavButton("◉", "Captura")
        self.btn_nav_capture.set_current(True)
        self.btn_cards = NavButton("▤", "Tarjetas")          # por ahora abren sus ventanas (se vuelven páginas en las etapas siguientes)
        self.btn_log = NavButton("☰", "Registro")
        self.btn_glossary = NavButton("✎", "Glosario")
        self.btn_settings = NavButton("⚙", "Configuración")
        self.btn_seg_frozen = QPushButton("Congelar")        # el selector «Al capturar» (combo_capture guarda el valor)
        self.btn_seg_live = QPushButton("En vivo")
        self.btn_copy_src = QPushButton("Copiar")
        self.btn_copy_dst = QPushButton("Copiar")
        self.btn_copy_src.setToolTip("Copia el texto original")
        self.btn_copy_dst.setToolTip("Copia la traducción")
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
        self.status = StatusLabel("Iniciando...")
        self.status.setObjectName("status")
        self.how_to = QLabel(
            "Tras elegir la región, la pantalla queda congelada con el texto encima. "
            "Esc la cierra. Esta ventana se minimiza al capturar y no vuelve a aparecer sola: "
            "cámbiate a ella desde la barra de tareas cuando quieras ver el registro.")
        self.how_to.setWordWrap(True)
        self.how_to.setObjectName("muted")
        self.dict_note = QLabel(
            "⚠ Diccionario no encontrado: ejecuta build_dictionary.py para ver "
            "significados al pasar el mouse sobre una palabra.")
        self.dict_note.setWordWrap(True)
        self.dict_note.setObjectName("warn")
        self.dict_note.setVisible(not self.dictionary.available)
        self.preview = QLabel("(aquí aparece la región capturada)")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(70)
        self.preview.setObjectName("preview")
        self.preview.setVisible(False)
        self.output = HoverTextEdit()
        self.output.setPlaceholderText("Texto original (OCR) de la última captura...")
        self.translation = QTextEdit()
        self.translation.setPlaceholderText("Traducción al español...")

        self.lbl_original = QLabel()
        self.lbl_translation = QLabel()
        self.lbl_original.setObjectName("sect")
        self.lbl_translation.setObjectName("sect")
        self.lbl_hint = QLabel("Mouse sobre una palabra: significado · clic: guardarla")
        self.lbl_hint.setObjectName("muted")
        for b_ in (self.btn_live_read, self.btn_capture):
            b_.setProperty("variant", "primary")
            b_.setMinimumHeight(46)                      # las dos filas de botones grandes miden lo mismo (primario y secundario)
        for b_ in (self.btn_live_text, self.btn_live_panel, self.btn_live_edit, self.btn_live_stop):
            b_.setMinimumHeight(36)
        for b_ in (self.btn_copy_src, self.btn_copy_dst):
            b_.setObjectName("ghost")
            b_.setCursor(Qt.PointingHandCursor)
        self.btn_live_stop.setProperty("variant", "danger")
        for b_ in (self.btn_seg_frozen, self.btn_seg_live):
            b_.setObjectName("segbtn")
            b_.setFocusPolicy(Qt.NoFocus)
            b_.setProperty("on", "false")

        # --- ventana: barra lateral a la izquierda, captura al centro ---
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.shell = QWidget()                      # la hoja de estilo vive aquí: las otras ventanas no la heredan
        self.shell.setObjectName("shell")
        self.shell.setStyleSheet(build_qss())
        outer.addWidget(self.shell)
        self.combo_capture.setParent(self.shell)    # guarda el modo (congelar / en vivo); los botones de arriba lo manejan
        self.combo_capture.hide()
        root = QHBoxLayout(self.shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 14, 14)
        sl.setSpacing(4)
        brand = QLabel("OCR Translator")
        brand.setObjectName("brand")
        sl.addWidget(brand)
        sl.addSpacing(12)
        for b_ in (self.btn_nav_capture, self.btn_cards, self.btn_log, self.btn_glossary, self.btn_settings):
            sl.addWidget(b_)
        sl.addStretch(1)
        self.profile_label.setObjectName("muted")
        sl.addWidget(self.profile_label)
        sl.addWidget(self.combo_profile)
        sl.addWidget(self.btn_profile_save)
        sl.addSpacing(8)
        sl.addWidget(self.status)
        root.addWidget(side)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(18, 16, 18, 14)
        cl.setSpacing(10)
        cl.addWidget(self.dict_note)
        seg = QFrame()
        seg.setObjectName("seg")
        seg_l = QHBoxLayout(seg)
        seg_l.setContentsMargins(4, 4, 4, 4)
        seg_l.setSpacing(4)
        seg_l.addWidget(self.btn_seg_frozen)
        seg_l.addWidget(self.btn_seg_live)
        row_a = QHBoxLayout()
        row_a.setSpacing(8)
        row_a.addWidget(self.combo_lang, 1)
        row_a.addWidget(self.combo_mode, 1)
        row_a.addWidget(seg)
        cl.addLayout(row_a)
        row_b = QHBoxLayout()
        row_b.setSpacing(8)
        row_b.addWidget(self.btn_capture, 1)
        row_b.addWidget(self.btn_live_read, 1)
        cl.addLayout(row_b)
        row_c = QHBoxLayout()
        row_c.setSpacing(8)
        for b_ in (self.btn_live_text, self.btn_live_panel, self.btn_live_edit, self.btn_live_stop):
            row_c.addWidget(b_, 1)
        cl.addLayout(row_c)
        cl.addWidget(self.preview)

        def card(title, edit, copy_btn):
            """Las dos tarjetas son idénticas por dentro (cabecera de la misma altura y campo debajo): así los campos quedan alineados."""
            frame = QFrame()
            frame.setObjectName("card")
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(14, 10, 14, 14)
            lay.setSpacing(8)
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(title, 1)
            head.addWidget(copy_btn)
            copy_btn.setFixedHeight(26)
            lay.addLayout(head)
            lay.addWidget(edit, 1)
            return frame
        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards.addWidget(card(self.lbl_original, self.output, self.btn_copy_src), 1)
        cards.addWidget(card(self.lbl_translation, self.translation, self.btn_copy_dst), 1)
        cl.addLayout(cards, 1)
        foot = QHBoxLayout()                          # casilla a la izquierda y la ayuda del mouse a la derecha
        foot.addWidget(self.chk_merge)
        foot.addStretch(1)
        self.lbl_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        foot.addWidget(self.lbl_hint)
        cl.addLayout(foot)
        cl.addWidget(self.how_to)
        self.stack = QStackedWidget()                 # páginas: 0 = Captura; Tarjetas se añade al abrirla
        self.stack.addWidget(content)
        root.addWidget(self.stack, 1)

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
        self.glossary_requested.connect(self.worker.translate_for_glossary)
        self.worker.glossary_translated.connect(self.on_glossary_translated)
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
        self.btn_live_stop.clicked.connect(lambda: self.live.stop())
        self.btn_seg_frozen.clicked.connect(lambda: self._set_capture_mode("frozen"))
        self.btn_seg_live.clicked.connect(lambda: self._set_capture_mode("live"))
        self.btn_settings.clicked.connect(lambda: self.open_settings())
        self.btn_profile_save.clicked.connect(self.save_profile_dialog)
        self._profile_loading = False
        self.btn_cards.clicked.connect(self.open_cards)
        self.btn_nav_capture.clicked.connect(self.show_capture_page)
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
        self.live_options = LiveOptions(self.settings)     # las casillas del modo en vivo (Configuración y tuerca del panel)
        self.live = LiveMode(self)
        self._sync_mode_bars()
        self._apply_hotkey_labels()
        self.live.active_changed.connect(lambda _a: self._update_capture_ui())
        self.live.view_changed.connect(self._sync_live_toggles)
        self.live.message.connect(self.status.setText)
        self.live.select_requested.connect(self.start_capture)
        saved = str(self.settings.value("capture_mode", "frozen"))
        self.combo_capture.setCurrentIndex(max(0, self.combo_capture.findData(saved)))

        self._reload_profiles()
        self.combo_profile.currentIndexChanged.connect(self._on_profile_chosen)
        self._apply_language_ui()
        self._update_buttons()
        self._update_capture_ui()

    # --- idioma ---
    def _dictionary_for(self, code):
        return self._dicts[code]

    def _apply_language_ui(self):
        pack = languages.active()
        self.words.dictionary = self._dictionary_for(pack.code)
        self.lbl_original.setText(f"ORIGINAL · {pack.source_name}")
        self.lbl_translation.setText(f"TRADUCCIÓN · {pack.target_name}")
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
        """Contadores de la barra lateral (y su descripción completa, con el idioma, al pasar el mouse)."""
        name = languages.active().source_name
        lang = languages.active().code
        cards, log, pending = len(self.store), len(self.log), self.glossary.count(lang, True)
        self.btn_cards.set_badge(str(cards) if cards else "")
        self.btn_cards.setToolTip(f"Palabras guardadas — {name} ({cards})")
        self.btn_log.set_badge(str(log) if log else "")
        self.btn_log.setToolTip(f"Registro de frases — {name} ({log})")
        self.btn_glossary.set_badge(str(pending) if pending else "", warn=True)
        self.btn_glossary.setToolTip(f"Pendientes — {name} ({pending})")

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

    def on_glossary_translated(self, entry_id, sentence_es):
        if self.words.glossary_translated(entry_id, sentence_es) and self.glossary_window is not None:
            self.glossary_window.reload()

    def _cards_changed(self):
        """Tras guardar o borrar: actualiza resaltado, contador y lista."""
        self.refresh_marks()
        self._update_buttons()
        if self.cards_window is not None:
            self.cards_window.reload()
        if self.glossary_window is not None:
            self.glossary_window.refresh_cards()

    def open_cards(self):
        """Muestra la página «Tarjetas» dentro de la ventana principal."""
        if self.cards_window is None:
            self.cards_window = CardsPage(self.store, self)
            self.cards_window.changed.connect(self._cards_changed)
            self.stack.addWidget(self.cards_window)
        self.cards_window.reload()
        self._show_page(self.cards_window, self.btn_cards)

    def show_capture_page(self):
        self._show_page(self.stack.widget(0), self.btn_nav_capture)

    def _show_page(self, page, nav_button):
        self.stack.setCurrentWidget(page)
        for b in (self.btn_nav_capture, self.btn_cards, self.btn_log, self.btn_glossary, self.btn_settings):
            b.set_current(b is nav_button)

    def open_glossary(self):
        """Muestra la página «Glosario» dentro de la ventana principal."""
        lang = languages.active().code
        if self.glossary_window is None:
            self.glossary_window = GlossaryWindow(
                self.glossary, lambda word: self.words.dictionary.key_for(word, ""), self,
                make_card=self.words.card_from_entry, has_card=self.words.has_card_for, settings=self.settings,
                preview_card=self.words.card_preview)
            self.glossary_window.changed.connect(self._cards_changed)
            self.stack.addWidget(self.glossary_window)
        self.glossary_window.set_language(lang)
        self._show_page(self.glossary_window, self.btn_glossary)

    def open_log(self):
        """Muestra la página «Registro» dentro de la ventana principal."""
        if self.log_window is None:
            self.log_window = LogPage(self.log, self.words, self, settings=self.settings)
            self.log_window.changed.connect(self._update_buttons)
            self.stack.addWidget(self.log_window)
        self.log_window.reload()
        self._show_page(self.log_window, self.btn_log)

    # --- flujo de captura ---
    def _minimize_windows(self) -> bool:
        """Minimiza las ventanas de la app para que no salgan en la captura (siguen en la barra
        de tareas y no se vuelven a abrir solas). ¿Alguna estaba a la vista?"""
        shown = False
        for w in (self,):
            if w is not None and w.isVisible() and not w.isMinimized():
                shown = True
                w.showMinimized()
        return shown

    # --- modo en vivo ---
    def _live_selected(self) -> bool:
        return self.combo_capture.currentData() == "live"

    def _set_capture_mode(self, data: str):
        """Los botones «Congelar» / «En vivo» de arriba cambian el modo (si el vivo no está en marcha)."""
        if self.combo_capture.isEnabled():
            self.combo_capture.setCurrentIndex(max(0, self.combo_capture.findData(data)))

    def _sync_live_toggles(self):
        """«Texto (O)» y «Panel (T)» se ven encendidos mientras el texto / el panel están a la vista."""
        on = self.live.active
        set_prop(self.btn_live_text, "on", on and self.live.text_visible)
        set_prop(self.btn_live_panel, "on", on and self.live.panel_visible)

    def on_capture_mode_changed(self):
        if self.live.active:                                   # con el vivo en marcha no se cambia de modo
            self.combo_capture.blockSignals(True)
            self.combo_capture.setCurrentIndex(self.combo_capture.findData("live"))
            self.combo_capture.blockSignals(False)
            return
        self.settings.setValue("capture_mode", self.combo_capture.currentData())
        self._update_capture_ui()

    def open_settings(self, tab: int = None):
        """Muestra la página «Configuración» (en la pestaña `tab`: 0 atajos, 1 captura congelada, 2 modo en vivo, 3 apariencia, 4 perfiles)."""
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.settings, self.live_options, self)
            self.settings_dialog.changed.connect(self.apply_settings)
            self.settings_dialog.profile_use.connect(self.apply_profile)
            self.settings_dialog.profile_save.connect(self.save_profile)
            self.settings_dialog.profiles_changed.connect(self._reload_profiles)
            self.stack.addWidget(self.settings_dialog)
        if tab is not None:
            self.settings_dialog.tabs.setCurrentIndex(tab)
        self._show_page(self.settings_dialog, self.btn_settings)

    # --- perfiles por juego ---
    def _reload_profiles(self):
        """Rellena el selector de perfiles con los guardados."""
        active = str(self.settings.value("profile_active", ""))
        self._profile_loading = True
        self.combo_profile.clear()
        self.combo_profile.addItem("(sin perfil)", "")
        for name in profiles.load(self.settings):
            self.combo_profile.addItem(name, name)
        self.combo_profile.setCurrentIndex(max(0, self.combo_profile.findData(active)))
        self._profile_loading = False

    def _on_profile_chosen(self):
        if self._profile_loading:
            return
        name = self.combo_profile.currentData()
        self.settings.setValue("profile_active", name or "")
        if name:
            self.apply_profile(name)

    def current_profile_data(self) -> dict:
        data = {"lang": str(self.combo_lang.currentData()), "mode": str(self.combo_mode.currentData()),
                "options": self.live_options.snapshot(),
                "pause": {"key": str(self.settings.value("live_pause_key", ""))}}
        if self.live.has_region() and self.live.rect is not None:
            r = self.live.rect
            data["box"] = [int(v) for v in self.live.box]
            data["rect"] = [r.x(), r.y(), r.width(), r.height()]
        return data

    def save_profile(self, name: str) -> bool:
        ok = profiles.save(self.settings, name, self.current_profile_data())
        if ok:
            self.settings.setValue("profile_active", name.strip())
            self._reload_profiles()
            if self.settings_dialog is not None:
                self.settings_dialog.reload_profiles()
        return ok

    def save_profile_dialog(self):
        name, ok = main_ui.ask_text(self, "Guardar perfil", "Nombre del juego o perfil:",
                                    str(self.settings.value("profile_active", "")))
        name = name.strip()
        if not ok or not name:
            return
        if name in profiles.load(self.settings) and not confirm(
                self, "Guardar perfil", f"Ya existe «{name}». ¿Reemplazarlo con la configuración actual?",
                yes_text="Reemplazar", no_text="Cancelar"):
            return
        if self.save_profile(name):
            self.status.setText(f"Perfil «{name}» guardado" + ("" if self.live.has_region() else " (sin región: elige una región fija para incluirla)") + ".")

    def apply_profile(self, name: str):
        """Pone idioma, modo de OCR, opciones, tecla de pausa y (si el perfil la tiene) la región fija del juego."""
        profile = profiles.load(self.settings).get(name)
        if profile is None:
            return
        self.settings.setValue("profile_active", name)
        if self.combo_profile.currentData() != name:
            self._reload_profiles()
        notes = []
        lang = profile.get("lang")
        if lang and self.combo_lang.findData(lang) >= 0 and lang != self.combo_lang.currentData():
            self.combo_lang.setCurrentIndex(self.combo_lang.findData(lang))
        mode = profile.get("mode")
        if mode and self.combo_mode.findData(mode) >= 0 and mode != self.combo_mode.currentData():
            self.combo_mode.setCurrentIndex(self.combo_mode.findData(mode))
        if "options" in profile:
            self.live_options.apply(profile["options"])
        if "pause" in profile:
            self.settings.setValue("live_pause_key", profile["pause"].get("key", ""))
            if self.settings_dialog is not None:
                self.settings_dialog.reload_pause_key()
        if "box" in profile:
            x, y, w, h = profile["rect"]
            rect = QRect(x, y, w, h)
            if any(not (s.geometry().intersected(rect)).isEmpty() and
                   s.geometry().intersected(rect).width() * s.geometry().intersected(rect).height() >= 0.5 * w * h
                   for s in QApplication.screens()):
                if self.combo_capture.currentData() != "live" and not self.live.active:
                    self.combo_capture.setCurrentIndex(self.combo_capture.findData("live"))
                self.on_live_region(tuple(profile["box"]), rect)
            else:
                notes.append("la región guardada queda fuera de las pantallas actuales: elígela de nuevo y guarda el perfil")
        self.status.setText(f"Perfil «{name}» aplicado" + (f" ({'; '.join(notes)})" if notes else "") + ".")
        if self.settings_dialog is not None:
            self.settings_dialog.reload_profiles()

    def apply_settings(self):
        """Atajos, textos con su atajo y valores numéricos del modo en vivo."""
        self.hotkey.set_bindings(load_bindings(self.settings))
        self.live.apply_settings()
        self.popup.set_scale(get_number(self.settings, "popup_scale"))
        if self.overlay is not None:
            self.overlay.set_keys(load_keys(self.settings, "frozen"))
            self.overlay.set_dim(get_number(self.settings, "frozen_dim"))
        self._sync_mode_bars()
        self._apply_hotkey_labels()
        self._update_capture_ui()

    def _apply_hotkey_labels(self):
        k = lambda a: hotkey_label(self.settings, a)          # noqa: E731
        self.btn_live_read.setText(f"Leer región ({k('read')})")
        self.btn_live_text.setToolTip(f"Muestra u oculta el texto dibujado sobre el original ({k('text')})")
        self.btn_live_panel.setToolTip(f"Muestra u oculta el panel con la traducción ({k('panel')})")
        self.btn_live_edit.setToolTip(f"Mueve o cambia el tamaño de la región fija ({k('edit')})")
        self.live.panel.btn_auto.setToolTip(f"Lee solo cuando el texto de la región cambia y se queda quieto ({k('auto')})")
        self.live.panel.btn_close.setToolTip(f"Cierra el panel (T o {k('panel')} lo vuelve a abrir)")

    def _update_capture_ui(self):
        live = self._live_selected()
        self.combo_capture.setEnabled(not self.live.active)
        tip = ("Detén el modo en vivo para cambiar de modo." if self.live.active
               else "Congelar: eliges una región en cada captura.\n"
                    "En vivo: eliges la región una vez y la lees cuando quieras.")
        self.combo_capture.setToolTip(tip)
        for b in (self.btn_seg_frozen, self.btn_seg_live):
            b.setEnabled(not self.live.active)
            b.setToolTip(tip)
        set_prop(self.btn_seg_live, "on", live)
        set_prop(self.btn_seg_frozen, "on", not live)
        set_prop(self.btn_capture, "variant", "primary" if not (live and self.live.active) else "")
        self.btn_live_read.setVisible(live)
        has_preview = self.preview.pixmap() is not None and not self.preview.pixmap().isNull()
        self.preview.setVisible(not live and has_preview)
        self._sync_live_toggles()
        self.btn_capture.setText(f"{'Elegir región fija' if live else 'Capturar región'} ({hotkey_label(self.settings, 'capture')})")
        self.combo_profile.setVisible(live)
        self.btn_profile_save.setVisible(live)
        self.profile_label.setVisible(live)
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

    def on_hotkey(self, action):
        if self.live.key_guard():                 # eco de la tecla de pausa que pulsamos nosotros
            return
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
        self._show_preview(image)
        self._request(image)

    def _show_preview(self, image):
        pix = bgr_to_qpixmap(image)
        self.preview.setPixmap(pix.scaled(480, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.preview.setVisible(not self._live_selected())

    def start_capture(self):
        if not self.btn_capture.isEnabled() or self._grabbing:
            return
        self._grabbing = True
        self.show_capture_page()                         # el resultado se ve en la página de captura
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
            self.overlay.set_dim(get_number(self.settings, "frozen_dim"))
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
        self._show_preview(image)
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
        if self._startup_notes:                          # p. ej. una base de datos dañada que se apartó al arrancar
            self.status.setText(" ".join(self._startup_notes) + "  " + self.status.text())
            self._startup_notes = []
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
