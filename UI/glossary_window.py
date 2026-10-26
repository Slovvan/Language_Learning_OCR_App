"""Ventana de Pendientes: palabras sin significado y tu glosario propio.

Escribe el significado de una palabra pendiente y desde ese momento el diccionario la muestra al
pasar el mouse. También se puede exportar la lista a un .tsv, rellenarla en una hoja de cálculo e
importarla de vuelta.
"""
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import Translator.languages as languages

EXPORT_DIR = Path(__file__).resolve().parent / "exports"


class GlossaryWindow(QWidget):
    """Lista de palabras del glosario (la más nueva arriba) con un editor para la seleccionada.
    Emite `changed` cuando algo cambia (marcas y contadores de la ventana principal)."""

    changed = pyqtSignal()

    def __init__(self, glossary, key_for, parent=None):
        super().__init__(parent, Qt.Window)
        self.glossary = glossary
        self.key_for = key_for                  # función palabra -> clave (la de la ventana principal)
        self.lang = "en"
        self._loading = False
        self._current_id = None
        self.resize(780, 660)

        self.only_pending = QCheckBox("Solo pendientes (sin significado)")
        self.count = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.only_pending)
        top.addStretch(1)
        top.addWidget(self.count)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Palabra", "Estado", "Significado", "Frase"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        self.ed_word = QLineEdit()
        self.ed_reading = QLineEdit()
        self.ed_reading.setPlaceholderText("Lectura en kana (opcional).")
        self.ed_meaning = QPlainTextEdit()
        self.ed_meaning.setPlaceholderText("Una línea por significado. Al escribirlo, la palabra deja de estar pendiente.")
        self.ed_sentence = QPlainTextEdit()
        for box in (self.ed_meaning, self.ed_sentence):
            box.setFixedHeight(66)
            box.setTabChangesFocus(True)
        form = QFormLayout()
        self.form = form
        form.addRow("Palabra", self.ed_word)
        form.addRow("Lectura", self.ed_reading)
        form.addRow("Significado", self.ed_meaning)
        form.addRow("Frase", self.ed_sentence)

        self.btn_delete = QPushButton("Eliminar seleccionadas")
        self.btn_export = QPushButton("Exportar (.tsv)")
        self.btn_export.setToolTip("Exporta las pendientes (o todas si desmarcas el filtro) para rellenarlas en una hoja de cálculo.")
        self.btn_import = QPushButton("Importar (.tsv)")
        self.btn_import.setToolTip("Lee el mismo formato: añade palabras nuevas y rellena los significados.")
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_delete)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_export)
        buttons.addWidget(self.btn_import)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.info)

        self.only_pending.toggled.connect(lambda _c: self.reload())
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.ed_word.textEdited.connect(lambda t: self._edited("word", t))
        self.ed_reading.textEdited.connect(lambda t: self._edited("reading", t))
        self.ed_meaning.textChanged.connect(lambda: self._edited("meaning", self.ed_meaning.toPlainText()))
        self.ed_sentence.textChanged.connect(lambda: self._edited("sentence", self.ed_sentence.toPlainText()))
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_export.clicked.connect(lambda: self.export())
        self.btn_import.clicked.connect(lambda: self.import_file())
        self.set_language(self.lang)

    def set_language(self, lang: str):
        self.lang = lang
        pack = languages.PACKS[lang]
        self.setWindowTitle(f"Pendientes y glosario — {pack.source_name}")
        self.form.labelForField(self.ed_reading).setVisible(lang == "ja")
        self.ed_reading.setVisible(lang == "ja")
        self.form.labelForField(self.ed_sentence).setText(f"Frase ({pack.source_name})")
        self.reload()

    # --- contenido ---
    def selected_ids(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def reload(self):
        keep = set(self.selected_ids())
        entries = self.glossary.all(self.lang, self.only_pending.isChecked())
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self._fill_row(row, e)
        self.table.clearSelection()
        for row, e in enumerate(entries):
            if e.id in keep:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self._on_selection()
        self._update_state()

    def _fill_row(self, row, e):
        word = f"{e.word}  【{e.reading}】" if e.reading else e.word
        values = (word, "pendiente" if e.pending else "con significado",
                  " · ".join(e.meaning.splitlines()), " ".join(e.sentence.split()))
        for col, value in enumerate(values):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, col, item)
            item.setText(value)
            item.setToolTip(value)
        self.table.item(row, 0).setData(Qt.UserRole, e.id)

    def _row_of(self, entry_id):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.UserRole) == entry_id:
                return row
        return -1

    def _update_state(self):
        total = self.glossary.count(self.lang)
        pending = self.glossary.count(self.lang, True)
        name = languages.PACKS[self.lang].source_name
        self.count.setText(f"{pending} pendiente(s) · {total - pending} con significado — {name}")
        self.btn_delete.setEnabled(bool(self.selected_ids()))
        self.btn_export.setEnabled(total > 0)

    # --- editor ---
    @staticmethod
    def _load(editor, text, only_if_changed):
        is_line = isinstance(editor, QLineEdit)
        current = editor.text() if is_line else editor.toPlainText()
        if only_if_changed and current == text:
            return
        editor.setText(text) if is_line else editor.setPlainText(text)

    def _on_selection(self):
        ids = self.selected_ids()
        previous = self._current_id
        self._current_id = ids[0] if len(ids) == 1 else None
        entry = self.glossary.get(self._current_id) if self._current_id is not None else None
        same = entry is not None and entry.id == previous
        self._loading = True
        self._load(self.ed_word, entry.word if entry else "", same)
        self._load(self.ed_reading, entry.reading if entry else "", same)
        self._load(self.ed_meaning, entry.meaning if entry else "", same)
        self._load(self.ed_sentence, entry.sentence if entry else "", same)
        self._loading = False
        for editor in (self.ed_word, self.ed_reading, self.ed_meaning, self.ed_sentence):
            editor.setEnabled(entry is not None)
        self._update_state()

    def _edited(self, field, text):
        if self._loading or self._current_id is None:
            return
        if field == "word" and not text.strip():
            return
        self.glossary.update(self._current_id, **{field: text})
        entry = self.glossary.get(self._current_id)
        row = self._row_of(self._current_id)
        if entry is not None and row >= 0:
            self._fill_row(row, entry)
        self._update_state()
        self.changed.emit()

    # --- acciones ---
    def delete_selected(self):
        ids = self.selected_ids()
        if ids and QMessageBox.question(
                self, "Eliminar", f"¿Eliminar {len(ids)} palabra(s) del glosario? No se puede deshacer."
        ) == QMessageBox.Yes:
            self.delete_ids(ids)

    def delete_ids(self, ids):
        self.glossary.delete(ids)
        self.reload()
        self.changed.emit()

    def export(self, path=None):
        only = self.only_pending.isChecked()
        if not self.glossary.count(self.lang, only):
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
            self.info.setText(f"No se pudo escribir el archivo: {exc}")
            return
        self.info.setText(f"✓ {count} palabra(s) exportada(s): {path}")

    def import_file(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Importar glosario", str(EXPORT_DIR),
                                                  "Texto con tabuladores (*.tsv *.txt)")
            if not path:
                return
        try:
            created, updated = self.glossary.import_tsv(self.lang, path, self.key_for)
        except (OSError, UnicodeDecodeError) as exc:
            self.info.setText(f"No se pudo leer el archivo: {exc}")
            return
        self.info.setText(f"✓ Importado: {created} nueva(s), {updated} actualizada(s).")
        self.reload()
        self.changed.emit()
