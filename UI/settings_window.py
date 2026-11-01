"""Ventana de configuración: atajos globales, teclas de la captura y comportamiento de la capa.

Los cambios se aplican al momento (no hay «Aceptar»): cada control escribe en AppSettings, que avisa
a quien lo necesite (atajos globales, barra de la capa…).
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

from UI.hotkeys import global_from_event, global_label, normalize_global, normalize_overlay, overlay_key_from_event
from UI.settings import DEFAULTS, GLOBAL_ACTIONS, OVERLAY_ACTIONS

TOOLBAR_CHOICES = (("hover", "En viñeta: una pestaña que se abre al pasar el mouse (no tapa la pantalla)"),
                   ("always", "Siempre visible"),
                   ("hidden", "Oculta (solo con las teclas)"))


class KeyCaptureButton(QPushButton):
    """Botón que muestra un atajo y, al pulsarlo, espera la nueva combinación."""
    captured = pyqtSignal(str)             # atajo nuevo (ya normalizado)

    def __init__(self, kind: str, value: str = ""):
        super().__init__()
        self.kind = kind                   # "global" (pynput) o "overlay" (Qt)
        self._value = value
        self._waiting = False
        self.setMinimumWidth(190)
        self.clicked.connect(self._start)
        self._show()

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str):
        self._value = value
        self._waiting = False
        self.releaseKeyboard()
        self._show()

    def label_of(self, value: str) -> str:
        return global_label(value) if self.kind == "global" else value

    def _show(self):
        self.setText("Pulsa la combinación…" if self._waiting else (self.label_of(self._value) or "(sin atajo)"))

    def _start(self):
        self._waiting = True
        self.grabKeyboard()
        self.setFocus()
        self._show()

    def _stop(self):
        self._waiting = False
        self.releaseKeyboard()
        self._show()

    def focusOutEvent(self, e):
        if self._waiting:
            self._stop()
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if not self._waiting:
            super().keyPressEvent(e)
            return
        key = e.key()
        if self.kind == "global":
            if key == Qt.Key_Escape:
                self._stop()
                return
            combo = global_from_event(e.modifiers(), key)
        else:
            combo = overlay_key_from_event(e.modifiers(), key)
        if combo is None:                  # solo un modificador: se sigue esperando
            return
        self._stop()
        self.captured.emit(combo)


class SettingsDialog(QDialog):
    def __init__(self, settings, hotkeys=None, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.settings = settings
        self.hotkeys = hotkeys
        self.setWindowTitle("Configuración")
        self.resize(640, 420)
        self._buttons = {}                 # clave de ajuste -> KeyCaptureButton
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #b3261e;")

        tabs = QTabWidget()
        tabs.addTab(self._global_tab(), "Atajos globales")
        tabs.addTab(self._overlay_tab(), "Teclas de la captura")
        tabs.addTab(self._capture_tab(), "Captura")
        self.tabs = tabs

        reset = QPushButton("Restaurar todo")
        reset.clicked.connect(self._reset_all)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(reset)
        bottom.addStretch(1)
        bottom.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        lay.addWidget(self.note)
        lay.addLayout(bottom)
        self.settings.changed.connect(lambda _k: self._sync())

    # ------------------------------------------------------------------ pestañas
    def _rows(self, specs, kind):
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        for row, (key, name, help_text) in enumerate(specs):
            setting = f"hotkey.{key}" if kind == "global" else f"overlay.key.{key}"
            label = QLabel(f"<b>{name}</b>" + (f"<br><span style='color:gray'>{help_text}</span>" if help_text else ""))
            label.setWordWrap(True)
            btn = KeyCaptureButton(kind, self.settings.get(setting))
            btn.captured.connect(lambda combo, s=setting, k=kind: self._assign(s, k, combo))
            default = QPushButton("Restaurar")
            default.clicked.connect(lambda _c=False, s=setting: self.settings.reset(s))
            grid.addWidget(label, row, 0)
            grid.addWidget(btn, row, 1)
            grid.addWidget(default, row, 2)
            self._buttons[setting] = btn
        return grid

    def _global_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Funcionan aunque el juego tenga el foco. Necesitan una combinación con Ctrl, Alt, Mayús o Win "
                      "(o una tecla F).")
        info.setWordWrap(True)
        lay.addWidget(info)
        if self.hotkeys is not None and not self.hotkeys.available:
            warn = QLabel("⚠ Los atajos globales no están disponibles: falta «pynput» (pip install pynput).")
            warn.setStyleSheet("color: #b3261e;")
            lay.addWidget(warn)
        lay.addLayout(self._rows([(k, n, h) for k, n, h, _d in GLOBAL_ACTIONS], "global"))
        lay.addStretch(1)
        return w

    def _overlay_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Teclas que se usan con la captura abierta. Pulsa un botón y luego la tecla nueva "
                      "(vale una sola tecla).")
        info.setWordWrap(True)
        lay.addWidget(info)
        lay.addLayout(self._rows([(k, n, "") for k, n, _d in OVERLAY_ACTIONS], "overlay"))
        lay.addStretch(1)
        return w

    def _capture_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.combo_toolbar = QComboBox()
        for value, text in TOOLBAR_CHOICES:
            self.combo_toolbar.addItem(text, value)
        self.combo_toolbar.setCurrentIndex(self.combo_toolbar.findData(self.settings.get("ui.toolbar")))
        self.combo_toolbar.currentIndexChanged.connect(
            lambda _i: self.settings.set("ui.toolbar", self.combo_toolbar.currentData()))
        form.addRow("Barra de controles de la captura\n(modos de OCR, unir líneas):", self.combo_toolbar)
        return w

    # ------------------------------------------------------------------ cambios
    def _assign(self, setting: str, kind: str, combo: str):
        norm = normalize_global(combo) if kind == "global" else normalize_overlay(combo)
        if not norm:
            self.note.setText("Esa combinación no es válida.")
            return
        prefix = "hotkey." if kind == "global" else "overlay.key."
        for other, btn in self._buttons.items():
            if other != setting and other.startswith(prefix) and btn.value == norm:
                self.note.setText(f"«{btn.label_of(norm)}» ya está asignada a otra acción. Cambia esa primero.")
                return
        self.note.setText("")
        self.settings.set(setting, norm)

    def _reset_all(self):
        self.settings.reset_all()
        self.note.setText("")

    def _sync(self):
        for setting, btn in self._buttons.items():
            btn.set_value(self.settings.get(setting))
        self.combo_toolbar.blockSignals(True)
        self.combo_toolbar.setCurrentIndex(self.combo_toolbar.findData(self.settings.get("ui.toolbar")))
        self.combo_toolbar.blockSignals(False)
