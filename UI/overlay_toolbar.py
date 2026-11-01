"""Barra de controles de la capa: modos de OCR (japonés), «unir líneas» y ajustes.

Tres formas de mostrarse (Ajustes ▸ Captura ▸ «Barra de controles»):
  siempre   la barra completa, siempre a la vista;
  viñeta    solo una pestaña pequeña «⚙ OCR»; se abre al pasar el mouse y se cierra sola (no tapa el juego);
  oculta    no se ve (todo se maneja con las teclas).
"""
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QPushButton, QToolButton

MODES = (("normal", "Normal"), ("kana", "Solo kana"), ("vertical", "Vertical"))
COLLAPSE_MS = 700

STYLE = """
OverlayToolbar { background: rgba(18, 20, 26, 235); border: 1px solid rgba(255,255,255,70); border-radius: 8px; }
QToolButton, QPushButton { color: #f2f2f2; background: rgba(255,255,255,18); border: 1px solid rgba(255,255,255,50);
    border-radius: 5px; padding: 3px 9px; font-size: 13px; }
QToolButton:hover, QPushButton:hover { background: rgba(255,255,255,45); }
QToolButton:checked { background: rgba(0,120,170,235); border-color: rgba(0,200,255,255); color: #ffffff; font-weight: bold; }
QPushButton#tab { font-weight: bold; }
"""


class OverlayToolbar(QFrame):
    mode_requested = pyqtSignal(str)       # "normal" / "kana" / "vertical"
    merge_toggled = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("OverlayToolbar")
        self.setStyleSheet(STYLE)
        self.setCursor(Qt.ArrowCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._policy = "hover"
        self._expanded = True
        self._has_modes = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(5)
        self.tab = QPushButton("⚙ OCR")
        self.tab.setObjectName("tab")
        self.tab.setFocusPolicy(Qt.NoFocus)
        self.tab.setToolTip("Pasa el mouse para ver los controles de OCR")
        lay.addWidget(self.tab)
        self.mode_buttons = {}
        for key, label in MODES:
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _c=False, k=key: self._mode_clicked(k))
            lay.addWidget(b)
            self.mode_buttons[key] = b
        self.merge = QToolButton()
        self.merge.setText("Unir líneas")
        self.merge.setCheckable(True)
        self.merge.setFocusPolicy(Qt.NoFocus)
        self.merge.clicked.connect(lambda _c=False: self.merge_toggled.emit())
        lay.addWidget(self.merge)
        self.gear = QToolButton()
        self.gear.setText("Ajustes…")
        self.gear.setFocusPolicy(Qt.NoFocus)
        self.gear.clicked.connect(lambda _c=False: self.settings_requested.emit())
        lay.addWidget(self.gear)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COLLAPSE_MS)
        self._timer.timeout.connect(self._collapse)
        self._refresh()

    # ------------------------------------------------------------------ API
    @property
    def policy(self) -> str:
        return self._policy

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_policy(self, policy: str):
        self._policy = policy if policy in ("always", "hover", "hidden") else "hover"
        self._expanded = self._policy == "always"
        self._timer.stop()
        self._refresh()

    def set_state(self, mode: str, merge: bool, has_modes: bool, keys: dict = None):
        """`keys`: {«normal»: «1», «merge»: «M»…} solo para mostrar en los textos de ayuda."""
        self._has_modes = has_modes
        for key, b in self.mode_buttons.items():
            b.setChecked(key == mode)
            if keys:
                b.setToolTip(f"Tecla {keys.get('mode_' + key, '')}".strip())
        self.merge.setChecked(bool(merge))
        if keys:
            self.merge.setToolTip(f"Tecla {keys.get('merge', '')}".strip())
        self._refresh()

    # ------------------------------------------------------------------ forma
    def _refresh(self):
        hidden = self._policy == "hidden"
        self.tab.setVisible(self._policy == "hover" and not self._expanded)
        show_body = (not hidden) and (self._policy == "always" or self._expanded)
        for b in self.mode_buttons.values():
            b.setVisible(show_body and self._has_modes)
        self.merge.setVisible(show_body)
        self.gear.setVisible(show_body)
        self.setVisible(not hidden)
        self.adjustSize()

    def _collapse(self):
        if self._policy == "hover" and not self.underMouse():
            self._expanded = False
            self._refresh()

    def _mode_clicked(self, key: str):
        for k, b in self.mode_buttons.items():
            b.setChecked(k == key)
        self.mode_requested.emit(key)

    def enterEvent(self, e):
        self._timer.stop()
        if self._policy == "hover" and not self._expanded:
            self._expanded = True
            self._refresh()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._policy == "hover":
            self._timer.start()
        super().leaveEvent(e)
