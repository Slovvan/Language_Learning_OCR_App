"""Ventana con la lista de palabras guardadas: ver, corregir, borrar y exportar a Anki."""
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import anki_export

EXPORT_DIR = Path(__file__).resolve().parent / "exports"


class CardsWindow(QWidget):
    """Lista de tarjetas (la más nueva arriba) con un editor para la seleccionada.

    Todo lo que escribes en el editor se guarda al momento. Emite `changed` cuando se borra
    alguna tarjeta, para que la ventana principal actualice las palabras resaltadas.
    """

    changed = pyqtSignal()

    def __init__(self, store, parent=None):
        super().__init__(parent, Qt.Window)
        self.store = store
        self._loading = False       # True mientras se rellenan los editores (no guardar)
        self._current_id = None     # tarjeta que se está editando
        self.setWindowTitle("Palabras guardadas")
        self.resize(780, 660)

        # --- lista ---
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Palabra", "Significado", "Frase"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)

        # --- editor de la tarjeta seleccionada ---
        self.ed_word = QLineEdit()
        self.ed_meaning = QPlainTextEdit()
        self.ed_sentence = QPlainTextEdit()
        self.ed_sentence_es = QPlainTextEdit()
        for box in (self.ed_meaning, self.ed_sentence, self.ed_sentence_es):
            box.setFixedHeight(66)
            box.setTabChangesFocus(True)
        self.ed_meaning.setPlaceholderText("Una línea por significado. Borra los que no quieras.")
        self.ed_sentence_es.setPlaceholderText("Traducción de la frase (se rellena sola al guardar la palabra).")
        form = QFormLayout()
        form.addRow("Palabra", self.ed_word)
        form.addRow("Significado", self.ed_meaning)
        form.addRow("Frase (EN)", self.ed_sentence)
        form.addRow("Traducción", self.ed_sentence_es)

        # --- botones ---
        self.btn_delete = QPushButton("Eliminar seleccionadas")
        self.btn_txt = QPushButton("Exportar para Anki (.txt)")
        self.btn_apkg = QPushButton("Exportar paquete (.apkg)")
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_delete)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_txt)
        buttons.addWidget(self.btn_apkg)
        self.count = QLabel()
        self.info = QLabel()                 # resultado de la última acción (exportar, etc.)
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.addWidget(self.count)
        layout.addWidget(self.table, 1)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.info)

        self.table.itemSelectionChanged.connect(self._on_selection)
        self.ed_word.textEdited.connect(lambda t: self._edited("word", t))
        self.ed_meaning.textChanged.connect(lambda: self._edited("meaning", self.ed_meaning.toPlainText()))
        self.ed_sentence.textChanged.connect(lambda: self._edited("sentence", self.ed_sentence.toPlainText()))
        self.ed_sentence_es.textChanged.connect(
            lambda: self._edited("sentence_es", self.ed_sentence_es.toPlainText()))
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_txt.clicked.connect(lambda: self.export("txt"))
        self.btn_apkg.clicked.connect(lambda: self.export("apkg"))
        self.reload()

    # --- contenido ---
    def selected_ids(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def reload(self):
        """Vuelve a leer la lista de la base de datos, conservando la selección."""
        keep = set(self.selected_ids())
        cards = list(reversed(self.store.all()))             # la más nueva primero
        self.table.blockSignals(True)
        self.table.setRowCount(len(cards))
        for row, card in enumerate(cards):
            self._fill_row(row, card)
        self.table.clearSelection()
        for row, card in enumerate(cards):
            if card.id in keep:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self._on_selection()
        self._update_state()

    def _fill_row(self, row, card):
        values = (card.word, " · ".join(card.meaning.splitlines()), card.sentence)
        for col, value in enumerate(values):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, col, item)
            item.setText(value)
            item.setToolTip(value)
        self.table.item(row, 0).setData(Qt.UserRole, card.id)

    def _row_of(self, card_id):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.UserRole) == card_id:
                return row
        return -1

    def _update_state(self):
        total = len(self.store)
        has_selection = bool(self.selected_ids())
        self.btn_delete.setEnabled(has_selection)
        self.btn_txt.setEnabled(total > 0)
        self.btn_apkg.setEnabled(total > 0)
        self.count.setText(f"{total} palabra(s) guardada(s)." if total else
                           "Aún no hay palabras. Haz clic en una palabra del texto original para guardarla.")

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
        self._load(self.ed_meaning, card.meaning if card else "", same_card)
        self._load(self.ed_sentence, card.sentence if card else "", same_card)
        self._load(self.ed_sentence_es, card.sentence_es if card else "", same_card)
        self._loading = False
        for editor in (self.ed_word, self.ed_meaning, self.ed_sentence, self.ed_sentence_es):
            editor.setEnabled(card is not None)
        self._update_state()

    def _edited(self, field, text):
        if self._loading or self._current_id is None:
            return
        if field == "word" and not text.strip():
            return                                           # la palabra no puede quedar vacía
        self.store.update(self._current_id, **{field: text})
        card = self.store.get(self._current_id)
        row = self._row_of(self._current_id)
        if card is not None and row >= 0:
            self._fill_row(row, card)

    # --- acciones ---
    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        answer = QMessageBox.question(
            self, "Eliminar", f"¿Eliminar {len(ids)} tarjeta(s)? Esto no se puede deshacer.")
        if answer == QMessageBox.Yes:
            self.delete_ids(ids)

    def delete_ids(self, ids):
        self.store.delete(ids)
        self.info.setText("")
        self.reload()
        self.changed.emit()

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
            default = str(EXPORT_DIR / f"vocabulario_{datetime.now():%Y%m%d_%H%M}{suffix}")
            path, _ = QFileDialog.getSaveFileName(self, "Exportar", default, label)
            if not path:
                return
        try:
            count = write(cards, path)
        except ImportError:
            self.info.setText("Para exportar el paquete .apkg instala genanki:  pip install genanki")
            return
        except OSError as exc:
            self.info.setText(f"No se pudo escribir el archivo: {exc}")
            return
        missing = sum(1 for c in cards if c.sentence and not c.sentence_es)
        note = f" ({missing} sin traducción de la frase)" if missing else ""
        self.info.setText(f"✓ {count} tarjeta(s) exportada(s){note}: {path}")