"""Página «Registro»: todas las frases capturadas, con su traducción, sin borrarse.

Arriba el título, la cuenta y el buscador; luego los filtros (Todas / Hoy / Ayer / Esta sesión) y el orden. La tabla ocupa
todo el espacio que queda. Al elegir UNA frase aparece, debajo de la tabla y siempre con la misma altura, el panel con el
original y la traducción (solo lectura; sobre el original funciona lo mismo que en la capa: significado al dejar el mouse
y clic para guardar la palabra) y la tabla se encoge. Con varias elegidas solo se ve la cuenta. Abajo, siempre en el mismo
sitio, eliminar y exportar. Esc quita la selección, Supr elimina, Ctrl+F busca.
"""
from datetime import datetime, timedelta
from pathlib import Path

from PyQt5.QtCore import QItemSelectionModel, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                             QPushButton, QSizePolicy, QSplitter, QSplitterHandle, QStackedWidget, QStyle,
                             QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

import Translator.languages as languages
import Cards.log_export as log_export
from UI.cards_window import _SearchBox
from UI.main_ui import confirm, set_prop
from UI.word_popup import HoverTextEdit

EXPORT_DIR = Path(__file__).resolve().parent / "exports"
SESSION_START = datetime.now()              # «Esta sesión» = desde que se abrió la aplicación
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
FILTERS = (("all", "Todas"), ("today", "Hoy"), ("yesterday", "Ayer"), ("session", "Esta sesión"))
COL_CHECK, COL_TIME, COL_SRC, COL_DST = range(4)
PANEL_HEIGHT = 224                          # alto inicial del panel Original / Traducción (no depende del texto)
PANEL_MIN = 170                             # lo más bajo que se puede dejar el panel arrastrando
TABLE_MIN = 120                             # y lo más bajo que se puede dejar la tabla
SETTING_PANEL = "log_panel_height"          # el alto elegido se recuerda entre sesiones


def one_line(text: str) -> str:
    return " ⏎ ".join(part.strip() for part in text.splitlines() if part.strip())


def parse_ts(ts: str):
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def format_time(ts: str, now=None) -> str:
    """Hora corta para la tabla: hoy «21:58:12», ayer «Ayer 21:58», antes «07 nov 21:58» (con año si es de otro año)."""
    when, now = parse_ts(ts), now or datetime.now()
    if when is None:
        return ts[:16]
    if when.date() == now.date():
        return when.strftime("%H:%M:%S")
    if when.date() == (now - timedelta(days=1)).date():
        return when.strftime("Ayer %H:%M")
    if when.year == now.year:
        return f"{when.day:02d} {MONTHS[when.month - 1]} {when:%H:%M}"
    return f"{when.day:02d} {MONTHS[when.month - 1]} {when.year}"


def in_range(ts: str, key: str, now=None, session_start=None) -> bool:
    if key == "all":
        return True
    now = now or datetime.now()
    if key == "session":
        when = parse_ts(ts)
        return when is not None and when >= (session_start or SESSION_START).replace(microsecond=0)
    day = (now - timedelta(days=1 if key == "yesterday" else 0)).strftime("%Y-%m-%d")
    return (ts or "")[:10] == day


class _CheckDelegate(QStyledItemDelegate):
    """Primera columna: casilla que se ve marcada cuando la fila está elegida."""

    def paint(self, p, option, index):
        super().paint(p, option, index)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(0, 0, 18, 18)
        box.moveCenter(QPointF(option.rect.center()))
        if option.state & QStyle.State_Selected:
            p.setBrush(QColor("#0b8fb8"))
            p.setPen(QPen(QColor("#38b6dc"), 1))
            p.drawRoundedRect(box, 5, 5)
            p.setPen(QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            x, y = box.left(), box.top()
            p.drawPolyline(QPointF(x + 4.5, y + 9.5), QPointF(x + 7.8, y + 12.5), QPointF(x + 13.5, y + 5.8))
        else:
            p.setBrush(QColor("#0e1015"))
            p.setPen(QPen(QColor("#4a5a73"), 1))
            p.drawRoundedRect(box, 5, 5)
        p.restore()


class _LogTable(QTableWidget):
    """Tabla de frases. Un clic en la casilla suma o quita esa fila de la selección; Supr elimina y Esc deselecciona."""
    delete_pressed = pyqtSignal()

    def mousePressEvent(self, event):
        index = self.indexAt(event.pos())
        if (event.button() == Qt.LeftButton and index.isValid() and index.column() == COL_CHECK
                and not event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)):
            self.selectionModel().select(index, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
            self.selectionModel().setCurrentIndex(index, QItemSelectionModel.NoUpdate)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_pressed.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape and self.selectionModel().hasSelection():
            self.clearSelection()
            event.accept()
            return
        super().keyPressEvent(event)


class _Grip(QSplitterHandle):
    """Asa para arrastrar el panel: una barrita centrada que se ilumina al pasar el mouse."""

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip("Arrastra para cambiar la altura del panel (se recuerda)")
        self._hover = False

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#6fd3ff" if self._hover else "#33405a"))
        bar = QRectF(0, 0, 44, 4)
        bar.moveCenter(QPointF(self.width() / 2, self.height() / 2))
        p.drawRoundedRect(bar, 2, 2)
        p.end()


class _Splitter(QSplitter):
    def createHandle(self):
        return _Grip(self.orientation(), self)


class LogPage(QWidget):
    """Lista de capturas con filtros, panel de la frase elegida y exportación."""

    changed = pyqtSignal()                        # se eliminaron frases (la ventana principal actualiza su contador)

    def __init__(self, log, words, parent=None, settings=None):
        super().__init__(parent)
        self.log = log
        self.words = words
        self._settings = settings                 # opcional: aquí se recuerda el alto del panel
        self._panel_user = settings is not None and settings.value(SETTING_PANEL) is not None   # ¿lo eligió la persona?
        self._panel_h = self._load_panel_height()
        self._current = None                      # LogEntry mostrada en el panel (None si no hay una sola elegida)
        self._selection = ()                      # ids elegidos la última vez (para saber si la selección cambió)
        self._range = "all"
        self._shown = 0
        self.setWindowTitle("Registro de frases")

        # --- cabecera ---
        self.title = QLabel("Registro")
        self.title.setObjectName("h1")
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.search = _SearchBox()
        self.search.setPlaceholderText("Buscar en el original o la traducción…")
        self.search.setClearButtonEnabled(True)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addSpacing(6)
        head.addWidget(self.search, 1)

        # --- filtros y orden ---
        self.chips = {}
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        for key, text in FILTERS:
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("on", "false")
            b.clicked.connect(lambda _c=False, k=key: self.set_range(k))
            self.chips[key] = b
            chips_row.addWidget(b)
        chips_row.addStretch(1)
        self.sort = QComboBox()
        self.sort.addItem("Más nuevas primero", "newest")
        self.sort.addItem("Más antiguas primero", "oldest")
        chips_row.addWidget(self.sort)

        # --- tabla ---
        self.table = _LogTable(0, 4)
        self.table.setHorizontalHeaderLabels(["", "HORA", "ORIGINAL", "TRADUCCIÓN"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setItemDelegateForColumn(COL_CHECK, _CheckDelegate(self.table))
        self.table.setMinimumHeight(120)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setFixedHeight(36)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(COL_CHECK, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_TIME, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_SRC, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_DST, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_CHECK, 46)
        self.table.setColumnWidth(COL_TIME, 112)
        self.table_card = QFrame()                 # el marco redondeado va aparte: así la selección no se sale de las esquinas
        self.table_card.setObjectName("card")
        card_lay = QVBoxLayout(self.table_card)
        card_lay.setContentsMargins(5, 5, 5, 5)
        card_lay.addWidget(self.table)
        self.message = QLabel()
        self.message.setObjectName("empty")
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.RichText)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table_card)
        self.stack.addWidget(self.message)

        # --- panel de la frase elegida (alto fijo) ---
        self.original = HoverTextEdit()
        self.original.setReadOnly(True)
        self.original.setToolTip("Mouse sobre una palabra: significado · clic: guardarla")
        self.translation = QTextEdit()
        self.translation.setReadOnly(True)
        for box in (self.original, self.translation):
            box.setMinimumHeight(44)
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            box.setTabChangesFocus(True)
        self.lbl_original = QLabel()
        self.lbl_original.setObjectName("sect")
        self.lbl_translation = QLabel()
        self.lbl_translation.setObjectName("sect")
        self.btn_copy_src = QPushButton("Copiar")
        self.btn_copy_dst = QPushButton("Copiar")
        for b in (self.btn_copy_src, self.btn_copy_dst):
            b.setObjectName("ghost")
            b.setFixedHeight(26)
        self.hint = QLabel("Mouse sobre una palabra del original: significado · clic: guardarla")
        self.hint.setObjectName("muted")

        def row(label, button, box):
            col = QVBoxLayout()
            col.setSpacing(4)
            top = QHBoxLayout()
            top.addWidget(label)
            top.addStretch(1)
            top.addWidget(button)
            col.addLayout(top)
            col.addWidget(box, 1)
            return col
        self.detail = QFrame()
        self.detail.setObjectName("card")
        self.detail.setMinimumHeight(PANEL_MIN)
        rows = QVBoxLayout(self.detail)
        rows.setContentsMargins(16, 10, 16, 8)
        rows.setSpacing(8)
        rows.addLayout(row(self.lbl_original, self.btn_copy_src, self.original), 1)      # una fila debajo de la otra
        rows.addLayout(row(self.lbl_translation, self.btn_copy_dst, self.translation), 1)
        rows.addWidget(self.hint)
        self.detail.setVisible(False)
        # tabla arriba y panel abajo, con un asa en medio para estirar el panel y leer textos largos
        self.table_card.setMinimumHeight(TABLE_MIN)
        self.split = _Splitter(Qt.Vertical)
        self.split.setHandleWidth(16)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.stack)
        self.split.addWidget(self.detail)
        self.split.setStretchFactor(0, 1)           # al cambiar el tamaño de la ventana, la tabla absorbe la diferencia
        self.split.setStretchFactor(1, 0)

        # --- barra de abajo ---
        self.btn_delete = QPushButton("Eliminar seleccionadas")
        self.btn_delete.setProperty("variant", "dangerghost")
        self.selected_label = QLabel()
        self.selected_label.setObjectName("selcount")
        self.btn_txt = QPushButton("Exportar (.txt)")
        self.btn_csv = QPushButton("Exportar (.csv)")
        self.btn_csv.setProperty("variant", "primary")
        for b in (self.btn_delete, self.btn_txt, self.btn_csv):
            b.setMinimumHeight(38)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.btn_delete)
        bar.addSpacing(6)
        bar.addWidget(self.selected_label)
        bar.addStretch(1)
        bar.addWidget(self.btn_txt)
        bar.addWidget(self.btn_csv)
        self.info = QLabel()                      # resultado de la última acción (exportar, guardar una palabra…)
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        layout.addLayout(head)
        layout.addLayout(chips_row)
        layout.addWidget(self.split, 1)
        layout.addWidget(self.info)
        layout.addLayout(bar)

        self.search.textChanged.connect(lambda _t: self.reload(user=True))
        self.sort.currentIndexChanged.connect(lambda _i: self.reload(user=True))
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.delete_pressed.connect(self.delete_selected)
        self.split.splitterMoved.connect(lambda _p, _i: self._remember_panel_height())
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_csv.clicked.connect(lambda: self.export("csv"))
        self.btn_txt.clicked.connect(lambda: self.export("txt"))
        self.btn_copy_src.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.original.toPlainText()))
        self.btn_copy_dst.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.translation.toPlainText()))
        self.original.word_context.connect(self.words.set_context)
        self.original.word_hovered.connect(self.words.hover)
        self.original.hover_ended.connect(self.words.hover_left)
        self.original.wheel_moved.connect(self.words.scroll_popup)
        self.original.word_clicked.connect(self._on_word_clicked)
        self.original.textChanged.connect(self.refresh_marks)
        self._style_chips()
        self.apply_language()
        self.reload()

    # --- idioma y filtros ---
    def apply_language(self):
        """Títulos según el idioma activo (cada idioma tiene su propio registro)."""
        pack = languages.active()
        self.setWindowTitle(f"Registro de frases — {pack.source_name}")
        self.lbl_original.setText(f"ORIGINAL · {pack.source_name}")
        self.lbl_translation.setText(f"TRADUCCIÓN · {pack.target_name}")
        self.original.setStyleSheet(f"QTextEdit {{ font-size:{19 if pack.code == 'ja' else 16}px; }}")

    def _style_chips(self):
        for key, b in self.chips.items():
            set_prop(b, "on", key == self._range)

    def set_range(self, key: str):
        if key not in self.chips or key == self._range:
            return
        self._range = key
        self._style_chips()
        self.reload(user=True)

    # --- contenido ---
    def selected_ids(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, COL_CHECK).data(Qt.UserRole) for r in rows]

    def _entries(self) -> list:
        entries = [e for e in self.log.all(self.search.text()) if in_range(e.ts, self._range)]
        if self.sort.currentData() == "oldest":
            entries.reverse()
        return entries

    def reload(self, user=False):
        """Vuelve a leer el registro (respetando búsqueda, filtro y orden) y conserva la selección.
        `user`: el cambio lo hizo la persona (búsqueda, filtro...) y no una captura nueva; sube la tabla al principio."""
        self.apply_language()
        keep = set(self.selected_ids())
        total = len(self.log)
        entries = self._entries()
        self._shown = len(entries)
        scroll = self.table.verticalScrollBar().value()
        now = datetime.now()
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            values = ("", format_time(e.ts, now), one_line(e.original), one_line(e.translation))
            for col, value in enumerate(values):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    if col == COL_TIME:
                        item.setForeground(QColor("#8da0b8"))
                    self.table.setItem(row, col, item)
                item.setText(value)
                item.setToolTip(e.ts if col == COL_TIME else value if col else "")
            self.table.item(row, COL_CHECK).setData(Qt.UserRole, e.id)
        self.table.clearSelection()
        for row, e in enumerate(entries):
            if e.id in keep:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self.table.verticalScrollBar().setValue(0 if user else scroll)
        self._show_content(total, len(entries))
        self._on_selection()
        self._update_state(total)

    def _show_content(self, total: int, shown: int):
        if shown:
            self.stack.setCurrentWidget(self.table_card)
            return
        if total == 0:
            text = ("<div style='font-size:16px; color:#ffffff;'>Aún no hay frases</div><br>"
                    "Captura una región del juego y cada frase leída quedará guardada aquí.")
        else:
            text = ("<div style='font-size:16px; color:#ffffff;'>Sin resultados</div><br>"
                    "Prueba con otra búsqueda o con otro filtro.")
        self.message.setText(text)
        self.stack.setCurrentWidget(self.message)

    def _update_state(self, total=None):
        total = len(self.log) if total is None else total
        name = languages.active().source_name
        word = "frase" if total == 1 else "frases"
        narrowed = bool(self.search.text().strip()) or self._range != "all"
        self.count.setText(f"{self._shown} de {total} {word} · {name}" if narrowed else f"{total} {word} · {name}")
        for w in (self.search, self.sort, *self.chips.values()):
            w.setEnabled(total > 0)
        self.btn_csv.setEnabled(self._shown > 0)
        self.btn_txt.setEnabled(self._shown > 0)

    @staticmethod
    def _set(box, text):
        if box.toPlainText() != text:             # no tocar lo que ya está (conserva hover y scroll)
            box.setPlainText(text)

    def _on_selection(self):
        ids = self.selected_ids()
        self._current = self.log.get(ids[0]) if len(ids) == 1 else None
        self._set(self.original, self._current.original if self._current else "")
        self._set(self.translation, self._current.translation if self._current else "")
        was = self.detail.isVisible()
        self.detail.setVisible(self._current is not None)
        if self._current is not None and not was:
            self._apply_panel_height()
            QTimer.singleShot(0, self._apply_panel_height)
            QTimer.singleShot(0, self._keep_selection_visible)      # la tabla se encogió: que la fila siga a la vista
        n = len(ids)
        self.selected_label.setText(f"{n} seleccionada{'s' if n != 1 else ''}" if n else "")
        self.btn_delete.setEnabled(bool(ids))
        what = f"las {n} seleccionadas" if n else "todas las que se ven"
        for b in (self.btn_csv, self.btn_txt):
            b.setToolTip(f"Exporta {what}.")
        if self.info.isVisible() and tuple(ids) != self._selection:
            self.show_message("")                  # el aviso de la acción anterior ya no viene al caso
        self._selection = tuple(ids)

    # --- alto del panel (lo elige la persona arrastrando el asa) ---
    def _load_panel_height(self) -> int:
        try:
            return max(PANEL_MIN, min(900, int(float(self._settings.value(SETTING_PANEL, PANEL_HEIGHT)))))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return PANEL_HEIGHT

    def _apply_panel_height(self):
        """Deja el panel con el alto elegido (o el que quepa si la ventana es baja) y la tabla con el resto."""
        if not self.detail.isVisibleTo(self):
            return
        total = sum(self.split.sizes()) or self.split.height()
        wanted = self._panel_h if self._panel_user else min(self._panel_h, max(PANEL_MIN, int(total * 0.52)))   # sin elección propia: no más de la mitad
        h = max(PANEL_MIN, min(wanted, total - TABLE_MIN - self.split.handleWidth()))
        self.split.setSizes([max(TABLE_MIN, total - h), h])

    def _remember_panel_height(self):
        if not self.detail.isVisibleTo(self):
            return
        self._panel_h = max(PANEL_MIN, self.detail.height())
        self._panel_user = True
        if self._settings is not None:
            try:
                self._settings.setValue(SETTING_PANEL, self._panel_h)
            except RuntimeError:
                pass

    def _keep_selection_visible(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if rows:
            self.table.scrollTo(self.table.model().index(rows[0], COL_SRC), QAbstractItemView.EnsureVisible)

    def refresh_marks(self):
        text = self.original.toPlainText()
        self.original.set_marks(self.words.marks(text), self.words.pending_marks(text))

    def _on_word_clicked(self, word, compound, start):
        merge = self._current.merged if self._current else True
        self.words.click(word, compound, self.original.toPlainText(), start, merge)

    def show_message(self, text):
        self.info.setText(text)
        self.info.setVisible(bool(text))

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Find):          # Ctrl+F: al buscador
            self.search.setFocus()
            self.search.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)

    # --- acciones ---
    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        n = len(ids)
        if confirm(self, "Eliminar", f"¿Eliminar {n} frase{'s' if n != 1 else ''} del registro? No se puede deshacer.",
                   yes_text="Eliminar", no_text="Cancelar", danger=True):
            self.delete_ids(ids)

    def export(self, kind: str, path=None):
        """Exporta las frases elegidas o, si no hay ninguna elegida, todas las que se ven (respeta búsqueda, filtro,
        orden e idioma activo). `kind` es «csv» o «txt»; sin `path` pregunta dónde."""
        ids = set(self.selected_ids())
        entries = [e for e in self._entries() if not ids or e.id in ids]
        if not entries:
            return
        suffix, label, write = ((".csv", "Hoja de cálculo (*.csv)", log_export.export_csv) if kind == "csv"
                                else (".txt", "Texto (*.txt)", log_export.export_txt))
        if path is None:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            default = str(EXPORT_DIR / f"frases_{entries[0].lang}_{datetime.now():%Y%m%d_%H%M}{suffix}")
            path, _ = QFileDialog.getSaveFileName(self, "Exportar frases", default, label)
            if not path:
                return
        try:
            count = write(entries, path)
        except OSError as exc:
            self.show_message(f"No se pudo escribir el archivo: {exc}")
            return
        self.show_message(f"✓ {count} frase(s) exportada(s): {path}")

    def delete_ids(self, ids):
        self.log.delete(ids)
        self.reload()
        self.changed.emit()

    def hideEvent(self, event):
        try:
            self.words.hide_popup()
        except RuntimeError:                       # la ventanita ya se destruyó (cierre de la aplicación)
            pass
        super().hideEvent(event)


LogWindow = LogPage          # nombre de cuando era una ventana suelta
