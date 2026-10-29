"""Ventana del registro de frases: todas las capturas, con su traducción, sin borrarse."""
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit,
                             QVBoxLayout, QWidget)

import Translator.languages as languages
from UI.word_popup import HoverTextEdit
import Cards.log_export as log_export

EXPORT_DIR = Path(__file__).resolve().parent / "exports"


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
        self.btn_csv = QPushButton("Exportar (.csv)")
        self.btn_csv.setToolTip("Exporta todas las frases que se ven (o solo las que elijas con Ctrl/Mayús).")
        self.btn_txt = QPushButton("Exportar (.txt)")
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_delete)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_csv)
        buttons.addWidget(self.btn_txt)
        buttons.addWidget(self.btn_copy_src)
        buttons.addWidget(self.btn_copy_dst)
        self.info = QLabel()

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 3)
        self.lbl_original = QLabel()
        self.lbl_translation = QLabel()
        layout.addWidget(self.lbl_original)
        layout.addWidget(self.original, 2)
        layout.addWidget(self.lbl_translation)
        layout.addWidget(self.translation, 2)
        layout.addLayout(buttons)
        layout.addWidget(self.info)

        self.search.textChanged.connect(lambda _t: self.reload())
        self.table.itemSelectionChanged.connect(self._on_selection)
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
        self.apply_language()
        self.reload()

    def apply_language(self):
        """Títulos según el idioma activo (cada idioma tiene su propio registro)."""
        pack = languages.active()
        self.setWindowTitle(f"Registro de frases — {pack.source_name}")
        self.lbl_original.setText(f"Original ({pack.source_name}) — mouse sobre una palabra: "
                                  "significado · clic: guardarla")
        self.lbl_translation.setText(f"Traducción ({pack.target_name})")

    # --- contenido ---
    def selected_ids(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def reload(self):
        """Vuelve a leer el registro (respetando la búsqueda) y conserva la selección."""
        self.apply_language()
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
        self.btn_csv.setEnabled(bool(entries))
        self.btn_txt.setEnabled(bool(entries))

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
        text = self.original.toPlainText()
        self.original.set_marks(self.words.marks(text), self.words.pending_marks(text))

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

    def export(self, kind: str, path=None):
        """Exporta todas las frases que se ven (respeta la búsqueda y el idioma activo). Si eliges
        varias en la lista (Ctrl o Mayús), solo esas. Una sola elegida no cuenta: la lista siempre
        deja una marcada para mostrarla abajo. `kind` es «csv» o «txt»; sin `path` pregunta dónde."""
        ids = set(self.selected_ids())
        if len(ids) < 2:
            ids = set()
        entries = [e for e in self.log.all(self.search.text()) if not ids or e.id in ids]
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
            self.info.setText(f"No se pudo escribir el archivo: {exc}")
            return
        self.info.setText(f"✓ {count} frase(s) exportada(s): {path}")

    def delete_ids(self, ids):
        self.log.delete(ids)
        self.reload()

    def hideEvent(self, event):
        self.words.hide_popup()
        super().hideEvent(event)
