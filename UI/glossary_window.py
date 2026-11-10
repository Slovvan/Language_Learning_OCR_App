"""Página «Glosario»: las palabras sin significado (pendientes) y las que rellenas tú.

Izquierda: la lista (la más nueva arriba) con buscador y filtro; cada fila muestra la palabra, su lectura y su
significado (o «— sin significado —» en ámbar). Derecha: «Vista previa» (la tarjeta tal como saldría en Anki, la misma vista
que en Tarjetas) o «Editar» (todo lo que escribes se guarda al momento). Debajo, los controles para crear la tarjeta.
Al escribir el significado de una pendiente, el diccionario la muestra desde ese momento al pasar el mouse. La lista se
exporta a un .tsv, se rellena en una hoja de cálculo y se importa de vuelta.
"""
import html
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QKeySequence, QPen
from PyQt5.QtWidgets import (QAbstractItemView, QBoxLayout, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QStyle,
                             QVBoxLayout, QWidget)

import Translator.languages as languages
from UI.cards_window import (ACCENT, MUTED, ROLE_ID, ROLE_JA, ROLE_SIDE, TEXT, CardPreview, _CardList, _RowDelegate,                     _SearchBox)
from UI.main_ui import confirm, set_prop

EXPORT_DIR = Path(__file__).resolve().parent / "exports"
ROLE_SUB, ROLE_PENDING, ROLE_CARD = Qt.UserRole + 3, Qt.UserRole + 4, Qt.UserRole + 5
AMBER, GREEN = "#e0a548", "#7fd6a4"
FILTERS = (("all", "Todas"), ("pending", "Pendientes"), ("done", "Con significado"))


class _EntryDelegate(_RowDelegate):
    """Fila de dos líneas: la palabra (grande) y su lectura a la derecha; debajo el significado, «— sin significado —»
    en ámbar si está pendiente y, a la derecha, una marca verde si ya tiene tarjeta."""

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 62)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(p.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            p.setBrush(QColor("#1b3a56"))
            p.setPen(QPen(QColor("#38b6dc"), 1))
            p.drawRoundedRect(r, 10, 10)
        elif option.state & QStyle.State_MouseOver:
            p.setBrush(QColor(255, 255, 255, 14))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(r, 10, 10)
        word, side = index.data(Qt.DisplayRole) or "", index.data(ROLE_SIDE) or ""
        sub, pending, has_card = index.data(ROLE_SUB) or "", bool(index.data(ROLE_PENDING)), bool(index.data(ROLE_CARD))
        ja = bool(index.data(ROLE_JA))
        inner = r.adjusted(14, 6, -14, -6)
        top = QRectF(inner.left(), inner.top(), inner.width(), 28)
        bottom = QRectF(inner.left(), inner.top() + 28, inner.width(), inner.height() - 28)
        f_side = option.font
        f_side.setPixelSize(13)
        f_side.setBold(False)
        p.setFont(f_side)
        side_w = min(QFontMetrics(f_side).horizontalAdvance(side) + 2, top.width() * 0.5) if side else 0
        if side:
            p.setPen(QColor(ACCENT))
            p.drawText(QRectF(top.right() - side_w, top.top(), side_w, top.height()), Qt.AlignRight | Qt.AlignVCenter,
                       QFontMetrics(f_side).elidedText(side, Qt.ElideRight, int(side_w)))
        f_word = option.font
        f_word.setPixelSize(20 if ja else 17)
        f_word.setBold(not ja)
        p.setFont(f_word)
        p.setPen(QColor("#ffffff" if selected else TEXT))
        room = max(20, int(top.width() - side_w - 10))
        p.drawText(QRectF(top.left(), top.top(), room, top.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f_word).elidedText(word, Qt.ElideRight, room))
        f_sub = option.font
        f_sub.setPixelSize(13)
        f_sub.setBold(False)
        p.setFont(f_sub)
        tag_w = 0
        if has_card:
            tag = "✓ tarjeta"
            tag_w = QFontMetrics(f_sub).horizontalAdvance(tag) + 4
            p.setPen(QColor(GREEN))
            p.drawText(QRectF(bottom.right() - tag_w, bottom.top(), tag_w, bottom.height()), Qt.AlignRight | Qt.AlignVCenter, tag)
        p.setPen(QColor(AMBER if pending else MUTED))
        room = max(20, int(bottom.width() - tag_w - 8))
        p.drawText(QRectF(bottom.left(), bottom.top(), room, bottom.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f_sub).elidedText(sub, Qt.ElideRight, room))
        p.restore()


class GlossaryPage(QWidget):
    """Lista de palabras del glosario con vista previa de su tarjeta, editor, filtros e importar/exportar.
    Emite `changed` cuando algo cambia (marcas y contadores de la ventana principal)."""

    changed = pyqtSignal()

    def __init__(self, glossary, key_for, parent=None, make_card=None, has_card=None, settings=None, preview_card=None):
        super().__init__(parent)
        self.glossary = glossary
        self.key_for = key_for                  # función palabra -> clave (la de la ventana principal)
        self.make_card = make_card              # entrada -> tarjeta nueva (o None); sin ella no hay controles de tarjeta
        self.has_card = has_card                # entrada -> ¿ya tiene tarjeta?
        self.preview_card = preview_card        # entrada -> tarjeta sin guardar (para la vista previa); opcional
        self.settings = settings                # QSettings: recuerda el interruptor «Crear tarjeta…»
        self.lang = "en"
        self._loading = False
        self._current_id = None
        self._mode = "preview"
        self._entries = []                      # todas las entradas del idioma, la más nueva primero
        self._shown = 0
        self.setWindowTitle("Pendientes y glosario")

        # --- cabecera: título, cuenta, buscador y filtro ---
        self.title = QLabel("Glosario")
        self.title.setObjectName("h1")
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.search = _SearchBox()
        self.search.setPlaceholderText("Buscar palabra, significado o frase…")
        self.search.setClearButtonEnabled(True)
        self.filter = QComboBox()
        for key, text in FILTERS:
            self.filter.addItem(text, key)
        self.filter.setToolTip("Qué palabras se ven (y se exportan).")
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addSpacing(6)
        head.addWidget(self.search, 1)
        head.addWidget(self.filter)

        # --- lista ---
        self.list = _CardList()
        self.list.setObjectName("cardlist")
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setItemDelegate(_EntryDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setFixedWidth(270)

        # --- selector Vista previa / Editar ---
        self.btn_preview = QPushButton("Vista previa")
        self.btn_edit = QPushButton("Editar")
        for b in (self.btn_preview, self.btn_edit):
            b.setObjectName("segbtn")
            b.setFocusPolicy(Qt.NoFocus)
            b.setProperty("on", "false")
        seg = self.seg = QFrame()
        seg.setObjectName("seg")
        seg_l = QHBoxLayout(seg)
        seg_l.setContentsMargins(4, 4, 4, 4)
        seg_l.setSpacing(4)
        seg_l.addWidget(self.btn_preview)
        seg_l.addWidget(self.btn_edit)
        mode_row = QHBoxLayout()
        mode_row.addWidget(seg)
        mode_row.addStretch(1)

        # --- páginas de la derecha: vista previa, editor y mensaje ---
        self.preview = CardPreview()
        self.editor = self._build_editor()
        self.message = QLabel()
        self.message.setObjectName("empty")
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.RichText)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.preview)
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.message)

        # --- crear tarjeta ---
        self.chk_auto_card = QCheckBox("Crear tarjeta al terminar")
        self.chk_auto_card.setToolTip("Cuando le escribes el significado a una palabra, se crea su tarjeta al pasar a otra palabra "
                                      "o al salir de esta página (con la frase guardada y su traducción).")
        self.btn_card = QPushButton("Guardar y crear tarjeta")
        self.btn_card.setMinimumHeight(34)
        self.btn_card.setToolTip("Crea ya la tarjeta de la palabra seleccionada, con tu significado, su frase y la traducción.")
        self.card_strip = QFrame()
        self.card_strip.setObjectName("card")
        strip = self._strip = QBoxLayout(QBoxLayout.LeftToRight, self.card_strip)   # en vertical cuando el panel es angosto
        strip.setContentsMargins(14, 8, 10, 8)
        strip.setSpacing(10)
        strip.addWidget(self.chk_auto_card, 1)
        strip.addWidget(self.btn_card)
        self.card_strip.setVisible(make_card is not None)
        if settings is not None:
            self.chk_auto_card.setChecked(settings.value("glossary_auto_card", False) in (True, "true", "1", 1))

        right = QVBoxLayout()
        right.setSpacing(10)
        right.addLayout(mode_row)
        right.addWidget(self.stack, 1)
        right.addWidget(self.card_strip)
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self.list)
        body.addLayout(right, 1)

        # --- barra de abajo ---
        self.btn_delete = QPushButton("Eliminar seleccionadas")
        self.btn_delete.setProperty("variant", "dangerghost")
        self.btn_import = QPushButton("Importar (.tsv)")
        self.btn_import.setToolTip("Lee el mismo formato: añade palabras nuevas y rellena los significados.")
        self.btn_export = QPushButton("Exportar (.tsv)")
        self.btn_export.setProperty("variant", "primary")
        self.btn_export.setToolTip("Exporta las pendientes (o todas) para rellenarlas en una hoja de cálculo.")
        for b in (self.btn_delete, self.btn_import, self.btn_export):
            b.setMinimumHeight(38)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.btn_delete)
        bar.addStretch(1)
        bar.addWidget(self.btn_import)
        bar.addWidget(self.btn_export)
        self.info = QLabel()                    # resultado de la última acción (exportar, importar, crear tarjeta…)
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)
        layout.addLayout(head)
        layout.addLayout(body, 1)
        layout.addWidget(self.info)
        layout.addLayout(bar)

        self.filter.currentIndexChanged.connect(lambda _i: self._refill(set(self.selected_ids())))
        self.search.textChanged.connect(lambda _t: self._refill(set(self.selected_ids())))
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.delete_pressed.connect(self.delete_selected)
        self.btn_preview.clicked.connect(lambda: self.set_mode("preview"))
        self.btn_edit.clicked.connect(lambda: self.set_mode("edit"))
        self.btn_back.clicked.connect(lambda: self.set_mode("preview"))
        self.ed_word.textEdited.connect(lambda t: self._edited("word", t))
        self.ed_reading.textEdited.connect(lambda t: self._edited("reading", t))
        self.ed_meaning.textChanged.connect(lambda: self._edited("meaning", self.ed_meaning.toPlainText()))
        self.ed_sentence.textChanged.connect(lambda: self._edited("sentence", self.ed_sentence.toPlainText()))
        self.ed_sentence_es.textChanged.connect(lambda: self._edited("sentence_es", self.ed_sentence_es.toPlainText()))
        self.chk_auto_card.toggled.connect(self._auto_card_toggled)
        self.btn_card.clicked.connect(self.create_card)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_export.clicked.connect(lambda: self.export())
        self.btn_import.clicked.connect(lambda: self.import_file())
        self.set_language(self.lang)

    # --- editor (el mismo formulario que en Tarjetas) ---
    def _build_editor(self) -> QWidget:
        def caption(text, top=8):
            label = QLabel(text)
            label.setObjectName("sect")
            label.setContentsMargins(0, top, 0, 0)
            return label
        self.ed_word = QLineEdit()
        self.ed_reading = QLineEdit()
        self.ed_reading.setPlaceholderText("Lectura en kana (opcional)")
        self.ed_meaning = QPlainTextEdit()
        self.ed_sentence = QPlainTextEdit()
        self.ed_sentence_es = QPlainTextEdit()
        for box, h in ((self.ed_meaning, 80), (self.ed_sentence, 58), (self.ed_sentence_es, 58)):
            box.setMinimumHeight(h)
            box.setTabChangesFocus(True)
        self.ed_meaning.setPlaceholderText("Una línea por significado. Al escribirlo, la palabra deja de estar pendiente.")
        self.ed_sentence_es.setPlaceholderText("Traducción de la frase (se rellena sola al pulsar la palabra)")
        self.cap_word, self.cap_reading = caption("PALABRA", 0), caption("LECTURA", 0)
        self.cap_meaning, self.cap_sentence, self.cap_sentence_es = caption("SIGNIFICADO"), caption("FRASE"), caption("TRADUCCIÓN")
        self.ok = QLabel("✓ Se guarda solo")
        self.ok.setObjectName("ok")
        self.btn_back = QPushButton("Vista previa")
        self.btn_back.setObjectName("ghost")
        self.btn_back.setFixedHeight(30)

        frame = QFrame()
        frame.setObjectName("card")
        grid = self._grid = QGridLayout(frame)
        grid.setContentsMargins(20, 16, 20, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        grid.addWidget(self.cap_word, 0, 0)
        grid.addWidget(self.cap_reading, 0, 1)
        grid.addWidget(self.ed_word, 1, 0)
        grid.addWidget(self.ed_reading, 1, 1)
        for row, (cap, ed) in enumerate(((self.cap_meaning, self.ed_meaning), (self.cap_sentence, self.ed_sentence),
                                         (self.cap_sentence_es, self.ed_sentence_es)), start=2):
            grid.addWidget(cap, row * 2 - 2, 0, 1, 2)
            grid.addWidget(ed, row * 2 - 1, 0, 1, 2)
        grid.setRowStretch(3, 1)
        grid.setRowStretch(5, 1)
        grid.setRowStretch(7, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 10, 0, 0)
        foot.addWidget(self.ok)
        foot.addStretch(1)
        foot.addWidget(self.btn_back)
        grid.addLayout(foot, 8, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        host = QWidget()                                   # en un área con desplazamiento: no impone alto mínimo a la ventana
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 6, 0)
        host_lay.addWidget(frame)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(host)
        return scroll

    STRIP_WIDE = 470        # por debajo, la casilla va sobre el botón

    def resizeEvent(self, e):
        super().resizeEvent(e)
        wide = self.card_strip.width() >= self.STRIP_WIDE
        self._strip.setDirection(QBoxLayout.LeftToRight if wide else QBoxLayout.TopToBottom)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Find):               # Ctrl+F: al buscador
            self.search.setFocus()
            self.search.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)

    # --- idioma ---
    def set_language(self, lang: str):
        self._auto_card(self._current_id)                  # al cambiar de idioma no se pierde la tarjeta pendiente de crear
        self.lang = lang
        pack = languages.PACKS[lang]
        self.setWindowTitle(f"Pendientes y glosario — {pack.source_name}")
        self.cap_sentence.setText(f"FRASE ({pack.source_name.upper()})")
        self.cap_sentence_es.setText(f"TRADUCCIÓN ({pack.target_name.upper()})")
        for w in (self.cap_reading, self.ed_reading):
            w.setVisible(lang == "ja")
        span = 1 if lang == "ja" else 2                    # sin lectura, la palabra ocupa todo el ancho
        self._grid.addWidget(self.cap_word, 0, 0, 1, span)
        self._grid.addWidget(self.ed_word, 1, 0, 1, span)
        self.reload()

    # --- contenido ---
    def selected_ids(self) -> list:
        return [i.data(ROLE_ID) for i in self.list.selectedItems()]

    def row_count(self) -> int:
        return self.list.count()

    def select_row(self, row: int):
        self.list.clearSelection()
        self.list.setCurrentRow(row)

    @property
    def filter_key(self) -> str:
        return self.filter.currentData()

    def set_filter(self, key: str):
        self.filter.setCurrentIndex(max(0, self.filter.findData(key)))

    def reload(self):
        """Vuelve a leer la lista de la base de datos, conservando la selección."""
        self._entries = self.glossary.all(self.lang)
        self._refill(set(self.selected_ids()))

    @staticmethod
    def _matches(e, needle: str) -> bool:
        return needle in " ".join((e.word, e.reading, e.meaning, e.sentence, e.sentence_es)).casefold()

    def _visible(self) -> list:
        needle = self.search.text().strip().casefold()
        kind = self.filter_key
        return [e for e in self._entries
                if (kind == "all" or (kind == "pending") == e.pending) and (not needle or self._matches(e, needle))]

    def _refill(self, keep: set):
        entries = self._visible()
        japanese = self.lang == "ja"
        bar = self.list.verticalScrollBar()
        scroll = bar.value()
        self.list.blockSignals(True)
        self.list.clear()
        for e in entries:
            item = QListWidgetItem()
            self._fill_item(item, e)
            self.list.addItem(item)
            if e.id in keep:
                item.setSelected(True)
        self.list.blockSignals(False)
        bar.setValue(scroll)
        self._shown = len(entries)
        has = bool(self._entries)
        self.list.setVisible(has)                          # sin palabras el mensaje ocupa todo el ancho
        self.seg.setVisible(has)
        self.card_strip.setVisible(has and self.make_card is not None)   # sin palabras no hay nada que crear
        self.filter.setEnabled(has)
        self.search.setEnabled(has)
        self._on_selection()
        self._update_state()

    def _has_card(self, entry) -> bool:
        return bool(self.has_card is not None and entry is not None and self.has_card(entry))

    def _fill_item(self, item, e):
        japanese = self.lang == "ja"
        item.setText(e.word)
        item.setData(ROLE_ID, e.id)
        item.setData(ROLE_SIDE, e.reading if japanese else "")
        item.setData(ROLE_JA, japanese)
        item.setData(ROLE_PENDING, e.pending)
        item.setData(ROLE_CARD, self._has_card(e))
        item.setData(ROLE_SUB, "— sin significado —" if e.pending else " · ".join(l.strip() for l in e.meaning.splitlines() if l.strip()))
        item.setToolTip(item.data(ROLE_SUB) if not e.pending else (e.sentence.strip() or "Sin significado todavía"))

    def refresh_cards(self):
        """Actualiza las marcas «✓ tarjeta» (se crearon o borraron tarjetas fuera de esta página)."""
        for row in range(self.list.count()):
            item = self.list.item(row)
            entry = self.glossary.get(item.data(ROLE_ID))
            if entry is not None:
                item.setData(ROLE_CARD, self._has_card(entry))
        self.list.viewport().update()
        self._update_card_button()

    def _row_of(self, entry_id):
        for row in range(self.list.count()):
            if self.list.item(row).data(ROLE_ID) == entry_id:
                return row
        return -1

    def _update_state(self):
        total = self.glossary.count(self.lang)
        pending = self.glossary.count(self.lang, True)
        name = languages.PACKS[self.lang].source_name
        word = "palabra" if total == 1 else "palabras"
        narrowed = bool(self.search.text().strip()) or self.filter_key != "all"
        self.count.setText(f"{self._shown} de {total} {word} · {name}" if narrowed else f"{total} {word} · {name}")
        for i, label in enumerate((f"Todas ({total})", f"Pendientes ({pending})", f"Con significado ({total - pending})")):
            self.filter.setItemText(i, label)
        self.btn_delete.setEnabled(bool(self.selected_ids()))
        self.btn_export.setEnabled(total > 0)
        self.btn_import.setEnabled(True)
        self._update_card_button()

    def _update_card_button(self):
        entry = self.glossary.get(self._current_id) if self._current_id is not None else None
        if entry is None:
            self.btn_card.setEnabled(False)
            self.btn_card.setToolTip("Elige una palabra.")
        elif entry.pending:
            self.btn_card.setEnabled(False)
            self.btn_card.setToolTip("Escribe primero el significado de la palabra.")
        elif self._has_card(entry):
            self.btn_card.setEnabled(False)
            self.btn_card.setToolTip("Esta palabra ya tiene tarjeta.")
        else:
            self.btn_card.setEnabled(self.make_card is not None)
            self.btn_card.setToolTip("Crea ya la tarjeta de la palabra seleccionada, con tu significado, su frase y la traducción.")

    # --- vista previa / editor ---
    def set_mode(self, mode: str):
        self._mode = "edit" if mode == "edit" else "preview"
        self._show_pane()

    def _preview_of(self, entry):
        """La tarjeta tal como se crearía; si no se puede construir, una aproximación con lo que hay."""
        card = None
        if self.preview_card is not None:
            try:
                card = self.preview_card(entry)
            except Exception:                              # noqa: BLE001   (la vista previa nunca debe romper la página)
                card = None
        if card is None:
            card = SimpleNamespace(word=entry.word, surface=entry.word, reading=entry.reading, meaning=entry.meaning,
                                   extra="", sentence=entry.sentence, sentence_es=entry.sentence_es, tags="", lang=entry.lang)
        return card

    def _show_pane(self):
        entry = self.glossary.get(self._current_id) if self._current_id is not None else None
        ids = self.selected_ids()
        single = entry is not None
        for b, on in ((self.btn_preview, self._mode == "preview"), (self.btn_edit, self._mode == "edit")):
            set_prop(b, "on", on and single)
            b.setEnabled(single)
        if single:
            if self._mode == "preview":
                self.preview.show_card(self._preview_of(entry), self.lang)
                self.stack.setCurrentWidget(self.preview)
            else:
                self.stack.setCurrentWidget(self.editor)
            return
        name = html.escape(languages.PACKS[self.lang].source_name)
        needle = self.search.text().strip()
        head = 'font-size:20px; font-weight:700; color:#c9d4e4;'
        sub = f'font-size:14px; color:{MUTED}; margin-top:8px;'
        if not self._entries:
            text = (f'<div style="{head}">Aún no hay palabras en {name}</div>'
                    f'<div style="{sub}">Las palabras sin significado que toques en el texto original aparecerán aquí '
                    'como pendientes.<br>También puedes importar un .tsv con tus palabras.</div>')
        elif len(ids) > 1:
            text = (f'<div style="{head}">{len(ids)} palabras seleccionadas</div>'
                    f'<div style="{sub}">Puedes eliminarlas con el botón de abajo.<br>'
                    'Elige una sola para ver su tarjeta o editarla.</div>')
        elif (needle or self.filter_key != "all") and not self._shown:
            what = f"con «{html.escape(needle)}»" if needle else "en este filtro"
            text = (f'<div style="{head}">Ninguna palabra coincide</div><div style="{sub}">{what}.</div>')
        else:
            text = (f'<div style="{head}">Elige una palabra</div>'
                    f'<div style="{sub}">Escribe su significado y el diccionario la mostrará al pasar el mouse.</div>')
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)

    @staticmethod
    def _load(editor, text, only_if_changed):
        """Pone `text` en el editor. Con `only_if_changed` no toca un editor que ya lo tiene,
        para no mover el cursor de quien está escribiendo."""
        is_line = isinstance(editor, QLineEdit)
        current = editor.text() if is_line else editor.toPlainText()
        if only_if_changed and current == text:
            return
        editor.setText(text) if is_line else editor.setPlainText(text)

    def _on_selection(self):
        ids = self.selected_ids()
        previous = self._current_id
        self._current_id = ids[0] if len(ids) == 1 else None
        if previous is not None and previous != self._current_id:
            self._auto_card(previous)                      # dejas la palabra: si está lista, se crea su tarjeta
        entry = self.glossary.get(self._current_id) if self._current_id is not None else None
        same = entry is not None and entry.id == previous
        self._loading = True
        self._load(self.ed_word, entry.word if entry else "", same)
        self._load(self.ed_reading, entry.reading if entry else "", same)
        self._load(self.ed_meaning, entry.meaning if entry else "", same)
        self._load(self.ed_sentence, entry.sentence if entry else "", same)
        self._load(self.ed_sentence_es, entry.sentence_es if entry else "", same)
        self._loading = False
        for editor in (self.ed_word, self.ed_reading, self.ed_meaning, self.ed_sentence, self.ed_sentence_es):
            editor.setEnabled(entry is not None)
        self._update_state()
        self._show_pane()

    def _edited(self, field, text):
        if self._loading or self._current_id is None:
            return
        if field == "word" and not text.strip():
            return                                         # la palabra no puede quedar vacía
        self.glossary.update(self._current_id, **{field: text})
        entry = self.glossary.get(self._current_id)
        row = self._row_of(self._current_id)
        if entry is not None:
            for i, e in enumerate(self._entries):
                if e.id == entry.id:
                    self._entries[i] = entry
                    break
        if entry is not None and row >= 0:
            self._fill_item(self.list.item(row), entry)
            self.list.viewport().update()
        self._update_state()
        self.changed.emit()

    # --- tarjetas ---
    def _auto_card_toggled(self, on):
        if self.settings is not None:
            self.settings.setValue("glossary_auto_card", bool(on))

    def _say(self, text: str):
        self.info.setText(text)
        self.info.setVisible(bool(text))

    def _refresh_row(self, entry_id):
        entry = self.glossary.get(entry_id)
        row = self._row_of(entry_id)
        if entry is not None and row >= 0:
            self._fill_item(self.list.item(row), entry)
            self.list.viewport().update()

    def _auto_card(self, entry_id):
        """Con «Crear tarjeta al guardar el significado» marcado: la entrada ya está lista y se crea su tarjeta."""
        if self.make_card is None or not self.chk_auto_card.isChecked() or entry_id is None:
            return
        entry = self.glossary.get(entry_id)
        if entry is None or entry.pending or self._has_card(entry):
            return
        if self.make_card(entry) is not None:
            self._refresh_row(entry_id)
            self._say(f"✓ Tarjeta creada: «{entry.word}».")
            self.changed.emit()

    def create_card(self):
        """«Guardar y crear tarjeta»: la de la palabra seleccionada, ahora mismo."""
        entry = self.glossary.get(self._current_id) if self._current_id is not None else None
        if entry is None or self.make_card is None:
            return
        if entry.pending:
            self._say("Escribe primero el significado de la palabra.")
            return
        if self.make_card(entry) is None:
            self._say(f"«{entry.word}» ya tiene tarjeta." if self._has_card(entry) else "No se creó la tarjeta.")
        else:
            self._say(f"✓ Tarjeta creada: «{entry.word}».")
            self.changed.emit()
        self._refresh_row(entry.id)
        self._update_card_button()

    def hideEvent(self, e):
        self._auto_card(self._current_id)                  # sales de la página
        super().hideEvent(e)

    # --- acciones ---
    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        n = len(ids)
        if confirm(self, "Eliminar", f"¿Eliminar {n} palabra{'s' if n != 1 else ''} del glosario? No se puede deshacer.",
                   yes_text="Eliminar", no_text="Cancelar", danger=True):
            self.delete_ids(ids)

    def delete_ids(self, ids):
        self.glossary.delete(ids)
        self._say("")
        self.reload()
        self.changed.emit()

    def export(self, path=None):
        """Exporta lo que muestra el filtro: las pendientes con «Pendientes» y todas con los otros dos."""
        only = self.filter_key == "pending"
        if not self.glossary.count(self.lang, only):
            self._say("No hay palabras pendientes para exportar." if only else "No hay palabras para exportar.")
            return
        if path is None:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            default = str(EXPORT_DIR / f"pendientes_{self.lang}_{datetime.now():%Y%m%d_%H%M}.tsv")
            path, _ = QFileDialog.getSaveFileName(self, "Exportar glosario", default, "Texto con tabuladores (*.tsv)")
            if not path:
                return
        try:
            count = self.glossary.export_tsv(self.lang, path, only)
        except OSError as exc:
            self._say(f"No se pudo escribir el archivo: {exc}")
            return
        self._say(f"✓ {count} palabra(s) exportada(s): {path}")

    def import_file(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Importar glosario", str(EXPORT_DIR),
                                                  "Texto con tabuladores (*.tsv *.txt)")
            if not path:
                return
        try:
            created, updated = self.glossary.import_tsv(self.lang, path, self.key_for)
        except (OSError, UnicodeDecodeError) as exc:
            self._say(f"No se pudo leer el archivo: {exc}")
            return
        self._say(f"✓ Importado: {created} nueva(s), {updated} actualizada(s).")
        self.reload()
        self.changed.emit()


GlossaryWindow = GlossaryPage          # nombre de cuando era una ventana suelta
