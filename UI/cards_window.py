"""Página «Tarjetas»: las palabras guardadas, con vista previa de la tarjeta, editor y exportación a Anki.

Izquierda: la lista (la más nueva arriba) con buscador. Derecha: «Vista previa» (anverso y reverso como saldrán en Anki)
o «Editar». Todo lo que escribes en el editor se guarda al momento. Emite `changed` cuando se borra alguna tarjeta,
para que la ventana principal actualice las palabras resaltadas.
"""
import html
import re
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPen, QTextOption
from PyQt5.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTextBrowser,
                             QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

import Cards.anki_export as anki_export
import Translator.languages as languages
from UI.main_ui import confirm, set_prop

EXPORT_DIR = Path(__file__).resolve().parent / "exports"
ROLE_ID, ROLE_SIDE, ROLE_JA = Qt.UserRole, Qt.UserRole + 1, Qt.UserRole + 2

MUTED, TEXT, ACCENT = "#7d8899", "#e8eef7", "#6fd3ff"
_LEMMA = re.compile(r"^(?P<lemma>[^:(\n]+):\s+(?=\()")


def _closing_paren(text: str) -> int:
    """Posición del «)» que cierra el «(» con el que empieza `text` (cuenta paréntesis anidados), o -1 si no cierra."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def split_meaning(line: str):
    """«(sust.) posada, fonda — a place…» -> (lema, tipo, significado, definición). Lo que no cuadre queda como significado.
    El tipo puede llevar paréntesis dentro: «(verbo (godan), trans.) hacer» -> tipo «verbo (godan), trans.»."""
    text = line.strip()
    lemma = ""
    m = _LEMMA.match(text)
    if m:
        lemma, text = m.group("lemma").strip(), text[m.end():]
    end = _closing_paren(text) if text.startswith("(") else -1
    if end < 0:
        return "", "", line.strip(), ""
    pos, body = text[1:end].strip(), text[end + 1:].strip()
    main, _sep, definition = body.partition(" — ")
    return lemma, pos, main.strip(), definition.strip()


def first_pos(meaning: str) -> str:
    """Tipo de palabra del primer significado («sustantivo»…), o vacío."""
    for line in meaning.splitlines():
        if line.strip():
            return split_meaning(line)[1]
    return ""


def highlight_sentence(card) -> str:
    """La frase en HTML, con la palabra tal como apareció resaltada (mismas reglas que la nota de Anki)."""
    text, surface = card.sentence.strip(), card.surface.strip()
    esc = html.escape
    if surface:
        pattern = re.escape(surface)
        if anki_export.profile_for([card])["word_boundaries"]:
            pattern = rf"(?<!\w){pattern}(?!\w)"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return (esc(text[:m.start()]) + f'<b style="color:{ACCENT}">' + esc(m.group()) + "</b>"
                    + esc(text[m.end():]))
    return esc(text)


def tag_pills(tags: str):
    """[(texto, clase)] de las etiquetas «JLPT N2 · Novelas: top 3 000»."""
    out = []
    for part in (tags or "").split(" · "):
        part = part.strip()
        if part:
            out.append((part, "jlpt" if part.startswith("JLPT") else "freq"))
    return out


class _SearchBox(QLineEdit):
    """Buscador: Esc lo vacía."""

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.text():
            self.clear()
            event.accept()
            return
        super().keyPressEvent(event)


class _CardList(QListWidget):
    """Lista de tarjetas: la tecla Supr pide eliminar las seleccionadas."""
    delete_pressed = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _RowDelegate(QStyledItemDelegate):
    """Fila de la lista: la palabra a la izquierda (grande) y, a la derecha, la lectura (japonés) o el tipo de palabra."""

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 48)

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
        word, side, ja = index.data(Qt.DisplayRole) or "", index.data(ROLE_SIDE) or "", bool(index.data(ROLE_JA))
        inner = r.adjusted(14, 0, -14, 0)
        f_side = option.font
        f_side.setPixelSize(13)
        f_side.setBold(False)
        p.setFont(f_side)
        side_w = min(QFontMetrics(f_side).horizontalAdvance(side) + 2, inner.width() * 0.6) if side else 0
        p.setPen(QColor(ACCENT))
        if side:
            p.drawText(QRectF(inner.right() - side_w, inner.top(), side_w, inner.height()), Qt.AlignRight | Qt.AlignVCenter,
                       QFontMetrics(f_side).elidedText(side, Qt.ElideRight, int(side_w)))
        f_word = option.font
        f_word.setPixelSize(20 if ja else 17)
        f_word.setBold(not ja)
        p.setFont(f_word)
        p.setPen(QColor("#ffffff" if selected else TEXT))
        room = max(20, int(inner.width() - side_w - 10))
        p.drawText(QRectF(inner.left(), inner.top(), room, inner.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f_word).elidedText(word, Qt.ElideRight, room))
        p.restore()


def _clear_layout(layout):
    """Vacía un layout y quita ya (no «más tarde») los widgets que tenía."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()


class _Pill(QLabel):
    """Etiqueta redondeada (tipo de palabra, nivel JLPT, frecuencia). Se pinta a mano: el estilo de Qt no redondea las etiquetas."""
    COLORS = {"pos": "#2a62a8", "jlpt": "#8e24aa", "freq": "#5e43b5"}

    def __init__(self, text: str, kind: str, parent=None):
        super().__init__(text, parent)
        self.kind = kind
        self.setStyleSheet("color:#ffffff; font-size:12px; font-weight:700; background:transparent; border:none; padding:0;")
        self.setContentsMargins(12, 3, 12, 3)
        self.setSizePolicy(self.sizePolicy().Maximum, self.sizePolicy().Fixed)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.COLORS.get(self.kind, "#3a475c")))
        p.drawRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
        p.end()
        super().paintEvent(event)


def _pill(text: str, kind: str) -> QLabel:
    return _Pill(text, kind)


class _PillBox(QWidget):
    """Fila de etiquetas que se parte en varias líneas cuando no caben. Las píldoras conservan su tamaño; lo que cambia es
    la altura de esta caja, que se calcula con el ancho disponible (nunca pide más ancho). `center` las centra por línea."""

    def __init__(self, center=True, top=0, parent=None):
        super().__init__(parent)
        self.center = center
        self.top = top
        self.gap = 8
        self._pills = []
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(0)

    def pills(self):
        return list(self._pills)

    def clear(self):
        for w in self._pills:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        self._pills = []
        self._relayout()

    def set_pills(self, pills):
        self.clear()
        for text, kind in pills:
            w = _pill(text, kind)
            w.setParent(self)
            w.show()
            self._pills.append(w)
        self._relayout()

    def _relayout(self):
        if not self._pills:
            self.setFixedHeight(0)
            return
        width = max(1, self.width())
        rows, cur, cur_w = [], [], 0
        for w in self._pills:
            pw = min(w.sizeHint().width(), width)
            need = pw + (self.gap if cur else 0)
            if cur and cur_w + need > width:
                rows.append((cur, cur_w))
                cur, cur_w, need = [], 0, pw
            cur.append((w, pw))
            cur_w += need
        rows.append((cur, cur_w))
        y = self.top
        for row, row_w in rows:
            h = max(w.sizeHint().height() for w, _pw in row)
            x = (width - row_w) // 2 if self.center else 0
            for w, pw in row:
                w.setGeometry(x, y + (h - w.sizeHint().height()) // 2, pw, w.sizeHint().height())
                x += pw + self.gap
            y += h + self.gap
        self.setFixedHeight(y - self.gap)

    def sizeHint(self):
        return QSize(0, self.height())

    def minimumSizeHint(self):
        return QSize(0, self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()


class _AutoText(QTextBrowser):
    """Texto con formato, de solo lectura, que crece hasta mostrarse entero y parte hasta las palabras interminables
    (un QLabel no las parte y ensancharía la ventana)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._html = ""
        self.setStyleSheet("QTextBrowser { background:transparent; border:none; padding:0; }")
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setOpenLinks(False)
        self.document().setDocumentMargin(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(40)

    def setText(self, html_text: str):
        self._html = html_text
        self.setHtml(f'<div align="center">{html_text}</div>')
        self._fit()

    def text(self) -> str:
        return self._html

    def _fit(self):
        width = max(40, self.viewport().width())
        self.document().setTextWidth(width)
        self.setFixedHeight(int(self.document().size().height()) + 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class _Face(QFrame):
    """Una cara de la tarjeta (anverso o reverso): título pequeño, contenido centrado y, abajo, etiquetas."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(22, 16, 22, 18)
        self._lay.setSpacing(8)
        head = QLabel(title)
        head.setObjectName("sect")
        self._lay.addWidget(head)
        self.body = _AutoText()
        self._lay.addWidget(self.body)
        self.pills = _PillBox(center=True, top=10)
        self._lay.addWidget(self.pills)
        self._lay.addStretch(1)

    def set(self, body_html: str, pills=(), center_pills=True):
        self.body.setText(body_html)
        self.pills.set_pills(pills)


class CardPreview(QScrollArea):
    """Cómo se verá la tarjeta en Anki: anverso (palabra y lectura o tipo) y reverso (significado, frase y traducción)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(12)
        self.front = _Face("ANVERSO")
        self.back = _Face("REVERSO")
        lay.addWidget(self.front)
        lay.addWidget(self.back)
        lay.addStretch(1)
        self.setWidget(inner)

    def show_card(self, card, lang: str):
        esc = html.escape
        ja = lang == "ja"
        size = 52 if ja else 46
        front = f'<div style="font-size:{size}px; font-weight:{400 if ja else 700}; color:#ffffff;">{esc(card.word)}</div>'
        pills = []
        if ja and card.reading:
            front += f'<div style="font-size:26px; color:{ACCENT}; margin-top:6px;">{esc(card.reading)}</div>'
        elif not ja and first_pos(card.meaning):
            pills = [(first_pos(card.meaning), "pos")]
        self.front.set(front, pills, center_pills=True)
        parts = []
        for line in card.meaning.splitlines():
            if not line.strip():
                continue
            lemma, pos, main, definition = split_meaning(line)
            row = ""
            if lemma:
                row += f'<span style="color:{MUTED}; font-size:14px;">{esc(lemma)}: </span>'
            if pos and ja:
                row += f'<span style="color:{MUTED}; font-size:13px;">{esc(pos)}  </span>'
            row += f'<span style="font-size:24px; font-weight:700; color:#ffffff;">{esc(main)}</span>'
            if definition:
                row += f'<br><span style="font-size:14px; color:{MUTED};">{esc(definition)}</span>'
            parts.append(f'<div style="margin:3px 0 7px 0;">{row}</div>')
        if not parts:
            parts.append(f'<div style="font-size:16px; color:{MUTED};">Sin significado todavía: escríbelo en «Editar».</div>')
        for line in card.extra.splitlines():
            if line.strip():
                parts.append(f'<div style="font-size:15px; color:{MUTED};">{esc(line.strip())}</div>')
        if card.sentence.strip():
            parts.append(f'<div style="font-size:21px; color:{TEXT}; margin-top:12px;">{highlight_sentence(card)}</div>')
            tr = card.sentence_es.strip()
            parts.append(f'<div style="font-size:16px; color:{MUTED if tr else "#5b6677"}; margin-top:3px;">'
                         f'{esc(tr) if tr else "Aún sin traducción de la frase"}</div>')
        self.back.set("".join(parts), tag_pills(card.tags))


class CardsPage(QWidget):
    """Lista de tarjetas (la más nueva arriba) con vista previa, editor, buscador y exportación."""

    changed = pyqtSignal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._loading = False       # True mientras se rellenan los editores (no guardar)
        self._current_id = None     # tarjeta que se está editando
        self._all = []              # todas las tarjetas del idioma, la más nueva primero
        self._mode = "preview"
        self.setWindowTitle("Palabras guardadas")

        # --- cabecera: título, cuenta y buscador ---
        self.title = QLabel("Tarjetas")
        self.title.setObjectName("h1")
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.search = _SearchBox()
        self.search.setPlaceholderText("Buscar palabra, significado o frase…")
        self.search.setClearButtonEnabled(True)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addSpacing(6)
        head.addWidget(self.search, 1)

        # --- lista ---
        self.list = _CardList()
        self.list.setObjectName("cardlist")
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setItemDelegate(_RowDelegate(self.list))
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
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addLayout(mode_row)
        right.addWidget(self.stack, 1)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self.list)
        body.addLayout(right, 1)

        # --- barra de abajo ---
        self.btn_delete = QPushButton("Eliminar seleccionadas")
        self.btn_delete.setProperty("variant", "dangerghost")
        self.btn_txt = QPushButton("Exportar para Anki (.txt)")
        self.btn_apkg = QPushButton("Exportar paquete (.apkg)")
        self.btn_apkg.setProperty("variant", "primary")
        for b in (self.btn_delete, self.btn_txt, self.btn_apkg):
            b.setMinimumHeight(38)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.btn_delete)
        bar.addStretch(1)
        bar.addWidget(self.btn_txt)
        bar.addWidget(self.btn_apkg)
        self.info = QLabel()                 # resultado de la última acción (exportar, etc.)
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

        self.list.itemSelectionChanged.connect(self._on_selection)
        self.search.textChanged.connect(lambda _t: self._refill(set(self.selected_ids())))
        self.btn_preview.clicked.connect(lambda: self.set_mode("preview"))
        self.btn_edit.clicked.connect(lambda: self.set_mode("edit"))
        self.btn_back.clicked.connect(lambda: self.set_mode("preview"))
        self.ed_word.textEdited.connect(lambda t: self._edited("word", t))
        self.ed_reading.textEdited.connect(lambda t: self._edited("reading", t))
        self.ed_meaning.textChanged.connect(lambda: self._edited("meaning", self.ed_meaning.toPlainText()))
        self.ed_sentence.textChanged.connect(lambda: self._edited("sentence", self.ed_sentence.toPlainText()))
        self.ed_sentence_es.textChanged.connect(
            lambda: self._edited("sentence_es", self.ed_sentence_es.toPlainText()))
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_txt.clicked.connect(lambda: self.export("txt"))
        self.btn_apkg.clicked.connect(lambda: self.export("apkg"))
        self.list.delete_pressed.connect(self.delete_selected)
        self.apply_language()
        self.reload()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Find):          # Ctrl+F: al buscador
            self.search.setFocus()
            self.search.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_editor(self) -> QWidget:
        def caption(text, top=8):
            label = QLabel(text)
            label.setObjectName("sect")
            label.setContentsMargins(0, top, 0, 0)
            return label
        self.ed_word = QLineEdit()
        self.ed_reading = QLineEdit()
        self.ed_reading.setPlaceholderText("Lectura en kana (se rellena sola con el diccionario)")
        self.ed_meaning = QPlainTextEdit()
        self.ed_sentence = QPlainTextEdit()
        self.ed_sentence_es = QPlainTextEdit()
        for box, h in ((self.ed_meaning, 96), (self.ed_sentence, 66), (self.ed_sentence_es, 66)):
            box.setMinimumHeight(h)
            box.setTabChangesFocus(True)
        self.ed_meaning.setPlaceholderText("Una línea por significado. Borra los que no quieras.")
        self.ed_sentence_es.setPlaceholderText("Traducción de la frase (se rellena sola al guardar la palabra)")
        self.cap_word, self.cap_reading = caption("PALABRA", 0), caption("LECTURA", 0)
        self.cap_meaning, self.cap_sentence, self.cap_sentence_es = caption("SIGNIFICADO"), caption("FRASE"), caption("TRADUCCIÓN")
        self.cap_tags = caption("ETIQUETAS")
        self.tags_row = _PillBox(center=False)
        self.ok = QLabel("✓ Los cambios se guardan solos")
        self.ok.setObjectName("ok")
        self.btn_back = QPushButton("Ver vista previa")
        self.btn_back.setObjectName("ghost")
        self.btn_back.setFixedHeight(30)

        frame = QFrame()
        frame.setObjectName("card")
        grid = QGridLayout(frame)
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
        grid.addWidget(self.cap_tags, 8, 0, 1, 2)
        grid.addWidget(self.tags_row, 9, 0, 1, 2)
        grid.setRowStretch(3, 1)
        grid.setRowStretch(5, 1)
        grid.setRowStretch(7, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 10, 0, 0)
        foot.addWidget(self.ok)
        foot.addStretch(1)
        foot.addWidget(self.btn_back)
        grid.addLayout(foot, 10, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        # En un área con desplazamiento: así el editor no impone un alto mínimo a toda la ventana (con etiquetas medía
        # ~50 px más y al reajustar el tamaño el contenido se salía por abajo).
        host = QWidget()
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 6, 0)
        host_lay.addWidget(frame)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(host)
        return scroll

    # --- idioma ---
    def apply_language(self):
        """Títulos y campos según el idioma activo (cada idioma tiene su propia lista)."""
        pack = languages.active()
        self.setWindowTitle(f"Palabras guardadas — {pack.source_name}")
        self.cap_sentence.setText(f"FRASE ({pack.source_name.upper()})")
        self.cap_sentence_es.setText(f"TRADUCCIÓN ({pack.target_name.upper()})")
        japanese = pack.code == "ja"
        for w in (self.cap_reading, self.ed_reading):
            w.setVisible(japanese)

    # --- contenido ---
    def selected_ids(self) -> list:
        return [i.data(ROLE_ID) for i in self.list.selectedItems()]

    def row_count(self) -> int:
        return self.list.count()

    def select_row(self, row: int):
        self.list.clearSelection()
        self.list.setCurrentRow(row)

    def _matches(self, card, needle: str) -> bool:
        hay = " ".join((card.word, card.surface, card.reading, card.meaning, card.sentence, card.sentence_es)).casefold()
        return needle in hay

    def reload(self):
        """Vuelve a leer la lista de la base de datos, conservando la selección."""
        self.apply_language()
        self._all = list(reversed(self.store.all()))             # la más nueva primero
        self._refill(set(self.selected_ids()))

    def _refill(self, keep: set):
        """Rellena la lista con las tarjetas que cumplen la búsqueda y reselecciona las de `keep`."""
        needle = self.search.text().strip().casefold()
        cards = [c for c in self._all if not needle or self._matches(c, needle)]
        japanese = languages.active().code == "ja"
        bar = self.list.verticalScrollBar()
        scroll = bar.value()
        self.list.blockSignals(True)
        self.list.clear()
        for card in cards:
            item = QListWidgetItem()
            self._fill_item(item, card, japanese)
            self.list.addItem(item)
            if card.id in keep:
                item.setSelected(True)
        self.list.blockSignals(False)
        bar.setValue(scroll)
        self._shown = len(cards)
        has = bool(self._all)
        self.list.setVisible(has)                     # sin tarjetas el mensaje ocupa todo el ancho
        self.seg.setVisible(has)
        self._on_selection()
        self._update_state()

    @staticmethod
    def _fill_item(item, card, japanese):
        item.setText(card.word)
        item.setData(ROLE_ID, card.id)
        item.setData(ROLE_SIDE, card.reading if japanese else first_pos(card.meaning))
        item.setData(ROLE_JA, japanese)
        item.setToolTip(" · ".join(card.meaning.splitlines()) or card.sentence)

    def _row_of(self, card_id):
        for row in range(self.list.count()):
            if self.list.item(row).data(ROLE_ID) == card_id:
                return row
        return -1

    def _update_state(self):
        total = len(self.store)
        name = languages.active().source_name
        shown = getattr(self, "_shown", total)
        word = "palabra" if total == 1 else "palabras"
        filtered = bool(self.search.text().strip())
        self.count.setText((f"{shown} de {total} {word} · {name}" if filtered else f"{total} {word} · {name}"))
        self.btn_delete.setEnabled(bool(self.selected_ids()))
        self.btn_txt.setEnabled(total > 0)
        self.btn_apkg.setEnabled(total > 0)

    # --- vista previa / editor ---
    def set_mode(self, mode: str):
        self._mode = "edit" if mode == "edit" else "preview"
        self._show_pane()

    def _show_pane(self):
        card = self.store.get(self._current_id) if self._current_id is not None else None
        ids = self.selected_ids()
        single = card is not None
        for b, on in ((self.btn_preview, self._mode == "preview"), (self.btn_edit, self._mode == "edit")):
            set_prop(b, "on", on and single)
            b.setEnabled(single)
        if single:
            if self._mode == "preview":
                self.preview.show_card(card, languages.active().code)
                self.stack.setCurrentWidget(self.preview)
            else:
                self.stack.setCurrentWidget(self.editor)
            return
        name = html.escape(languages.active().source_name)
        needle = self.search.text().strip()
        if not self._all:
            text = (f'<div style="font-size:20px; font-weight:700; color:#c9d4e4;">Aún no hay palabras en {name}</div>'
                    f'<div style="font-size:14px; color:{MUTED}; margin-top:8px;">Haz clic en una palabra del texto original '
                    'para guardarla.<br>Aquí aparecerá la tarjeta, lista para exportar a Anki.</div>')
        elif len(ids) > 1:
            text = (f'<div style="font-size:20px; font-weight:700; color:#c9d4e4;">{len(ids)} tarjetas seleccionadas</div>'
                    f'<div style="font-size:14px; color:{MUTED}; margin-top:8px;">Puedes eliminarlas con el botón de abajo.<br>'
                    'Elige una sola para ver su vista previa o editarla.</div>')
        elif needle and not self._shown:
            text = (f'<div style="font-size:20px; font-weight:700; color:#c9d4e4;">Ninguna tarjeta coincide</div>'
                    f'<div style="font-size:14px; color:{MUTED}; margin-top:8px;">con «{html.escape(needle)}».</div>')
        else:
            text = (f'<div style="font-size:20px; font-weight:700; color:#c9d4e4;">Elige una tarjeta</div>'
                    f'<div style="font-size:14px; color:{MUTED}; margin-top:8px;">Verás cómo queda en Anki y podrás corregirla.</div>')
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)

    # --- editor ---
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
        card = self.store.get(self._current_id) if self._current_id is not None else None
        # Misma tarjeta (p. ej. llegó la traducción de la frase): solo se actualiza lo que cambió.
        # Otra tarjeta: se recarga todo, lo que también borra el historial de «deshacer».
        same_card = card is not None and card.id == previous
        self._loading = True
        self._load(self.ed_word, card.word if card else "", same_card)
        self._load(self.ed_reading, card.reading if card else "", same_card)
        self._load(self.ed_meaning, card.meaning if card else "", same_card)
        self._load(self.ed_sentence, card.sentence if card else "", same_card)
        self._load(self.ed_sentence_es, card.sentence_es if card else "", same_card)
        self._loading = False
        for editor in (self.ed_word, self.ed_reading, self.ed_meaning, self.ed_sentence, self.ed_sentence_es):
            editor.setEnabled(card is not None)
        self._fill_tags(card)
        self._update_state()
        self._show_pane()

    def _fill_tags(self, card):
        pills = tag_pills(card.tags) if card else []
        self.tags_row.set_pills(pills)
        self.cap_tags.setVisible(bool(pills))

    def _edited(self, field, text):
        if self._loading or self._current_id is None:
            return
        if field == "word" and not text.strip():
            return                                           # la palabra no puede quedar vacía
        self.store.update(self._current_id, **{field: text})
        card = self.store.get(self._current_id)
        row = self._row_of(self._current_id)
        if card is not None and row >= 0:
            self._fill_item(self.list.item(row), card, languages.active().code == "ja")
            for i, c in enumerate(self._all):
                if c.id == card.id:
                    self._all[i] = card
                    break
            self.list.viewport().update()

    # --- acciones ---
    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        n = len(ids)
        if confirm(self, "Eliminar", f"¿Eliminar {n} tarjeta{'s' if n != 1 else ''}? Esto no se puede deshacer.",
                   yes_text="Eliminar", no_text="Cancelar", danger=True):
            self.delete_ids(ids)

    def delete_ids(self, ids):
        self.store.delete(ids)
        self.info.setText("")
        self.info.setVisible(False)
        self.reload()
        self.changed.emit()

    def _say(self, text: str):
        self.info.setText(text)
        self.info.setVisible(bool(text))

    def export(self, kind: str, path=None):
        """Exporta todas las tarjetas. `kind` es «txt» o «apkg»; sin `path` pregunta dónde."""
        cards = self.store.all()
        if not cards:
            return
        if kind == "txt":
            suffix, label, write = ".txt", "Texto para Anki (*.txt)", anki_export.export_tsv
        else:
            suffix, label, write = ".apkg", "Paquete de Anki (*.apkg)", anki_export.export_apkg
        if path is None:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            lang = cards[0].lang or "en"
            default = str(EXPORT_DIR / f"vocabulario_{lang}_{datetime.now():%Y%m%d_%H%M}{suffix}")
            path, _ = QFileDialog.getSaveFileName(self, "Exportar", default, label)
            if not path:
                return
        try:
            count = write(cards, path)
        except ImportError:
            self._say("Para exportar el paquete .apkg instala genanki:  pip install genanki")
            return
        except OSError as exc:
            self._say(f"No se pudo escribir el archivo: {exc}")
            return
        missing = sum(1 for c in cards if c.sentence and not c.sentence_es)
        note = f" ({missing} sin traducción de la frase)" if missing else ""
        self._say(f"✓ {count} tarjeta(s) exportada(s){note}: {path}")


CardsWindow = CardsPage          # nombre de antes
