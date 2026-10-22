"""Ventana del registro de frases: todas las capturas, con su traducción, sin borrarse."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit,
                             QVBoxLayout, QWidget)

from UI.word_popup import HoverTextEdit


def one_line(text: str) -> str:
    return " ⏎ ".join(part.strip() for part in text.splitlines() if part.strip())


class LogWindow(QWidget):
    """Lista de capturas (la más nueva arriba). Al elegir una se ve completa; sobre su texto
    original funciona lo mismo que en la capa: significado al dejar el mouse y clic para guardar."""

    def __init__(self, log, words, parent=None):
        super().__init__(parent, Qt.Window)
        self.log = log
        self.words = words
        self._current = None                      # LogEntry mostrada abajo
        self.setWindowTitle("Registro de frases")
        self.resize(820, 700)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar en el original o la traducción…")
        self.search.setClearButtonEnabled(True)
        self.count = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.count)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Hora", "Original", "Traducción"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)

        self.original = HoverTextEdit()
        self.original.setReadOnly(True)
        self.original.setPlaceholderText("Elige una frase de la lista.")
        self.translation = QTextEdit()
        self.translation.setReadOnly(True)

        self.btn_delete = QPushButton("Eliminar seleccionadas")
        self.btn_copy_src = QPushButton("Copiar original")
        self.btn_copy_dst = QPushButton("Copiar traducción")
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_delete)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_copy_src)
        buttons.addWidget(self.btn_copy_dst)
        self.info = QLabel()

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 3)
        layout.addWidget(QLabel("Original — mouse sobre una palabra: significado · clic: guardarla"))
        layout.addWidget(self.original, 2)
        layout.addWidget(QLabel("Traducción"))
        layout.addWidget(self.translation, 2)
        layout.addLayout(buttons)
        layout.addWidget(self.info)

        self.search.textChanged.connect(lambda _t: self.reload())
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_copy_src.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.original.toPlainText()))
        self.btn_copy_dst.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.translation.toPlainText()))
        self.original.word_hovered.connect(self.words.hover)
        self.original.hover_ended.connect(self.words.hide_popup)
        self.original.word_clicked.connect(self._on_word_clicked)
        self.original.textChanged.connect(self.refresh_marks)
        self.reload()

    # --- contenido ---
    def selected_ids(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def reload(self):
        """Vuelve a leer el registro (respetando la búsqueda) y conserva la selección."""
        keep = set(self.selected_ids())
        entries = self.log.all(self.search.text())
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            for col, value in enumerate((e.ts[5:16], one_line(e.original), one_line(e.translation))):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, col, item)
                item.setText(value)
                item.setToolTip(value if col else e.ts)
            self.table.item(row, 0).setData(Qt.UserRole, e.id)
        self.table.clearSelection()
        for row, e in enumerate(entries):
            if e.id in keep:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        if not self.selected_ids() and entries:
            self.table.selectRow(0)               # nada elegido: se ve la más nueva
        self._on_selection()
        total = len(self.log)
        self.count.setText(f"{len(entries)} de {total}" if self.search.text().strip() else f"{total} frase(s)")

    @staticmethod
    def _set(box, text):
        if box.toPlainText() != text:             # no tocar lo que ya está (conserva hover y scroll)
            box.setPlainText(text)

    def _on_selection(self):
        ids = self.selected_ids()
        self._current = self.log.get(ids[0]) if len(ids) == 1 else None
        self._set(self.original, self._current.original if self._current else "")
        self._set(self.translation, self._current.translation if self._current else "")
        self.btn_delete.setEnabled(bool(ids))

    def refresh_marks(self):
        self.original.set_marks(self.words.marks(self.original.toPlainText()))

    def _on_word_clicked(self, word, compound, start):
        merge = self._current.merged if self._current else True
        self.words.click(word, compound, self.original.toPlainText(), start, merge)

    def show_message(self, text):
        self.info.setText(text)

    # --- acciones ---
    def delete_selected(self):
        ids = self.selected_ids()
        if ids and QMessageBox.question(
                self, "Eliminar", f"¿Eliminar {len(ids)} frase(s) del registro? No se puede deshacer."
        ) == QMessageBox.Yes:
            self.delete_ids(ids)

    def delete_ids(self, ids):
        self.log.delete(ids)
        self.reload()

    def hideEvent(self, event):
        self.words.hide_popup()
        super().hideEvent(event)
