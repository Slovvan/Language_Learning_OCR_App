"""Barra de modos de OCR del japonés: Normal · Solo kana · Vertical.

Una sola píldora con tres segmentos (el activo resaltado). Según la preferencia («Apariencia» en Configuración):
  siempre   los tres segmentos a la vista;
  viñeta    una píldora pequeña con el modo actual («あ Solo kana ▾») que se abre al pasar el mouse y se pliega sola;
  oculta    no se ve (se cambia con las teclas 1 / 2 / 3).
La usan la captura congelada (flotando arriba) y el panel del modo en vivo (integrada en su cabecera).
"""
from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QToolButton

MODES = (("normal", "Normal", "Con kanji: la lectura de siempre"),
         ("kana", "Solo kana", "Juegos sin kanji (todo hiragana / katakana): corrige más confusiones"),
         ("vertical", "Vertical", "Texto en columnas, de derecha a izquierda"))
POLICIES = ("hover", "always", "hidden")
COLLAPSE_MS = 700

STYLE = """
ModeBar { background: rgba(18, 20, 26, 238); border: 1px solid rgba(255,255,255,60); border-radius: 15px; }
ModeBar QLabel { color: #8f9bb0; font-size: 14px; font-weight: bold; background: transparent; padding-left: 8px; padding-right: 2px; }
ModeBar QToolButton { color: #c9d1dc; background: transparent; border: none; border-radius: 12px;
                      padding: 4px 13px; font-size: 13px; }
ModeBar QToolButton:hover { background: rgba(255,255,255,30); }
ModeBar QToolButton:checked { background: rgb(0,124,168); color: white; font-weight: 600; }
ModeBar QToolButton#chip { padding: 4px 12px 4px 0px; color: #e6ebf2; }
"""


class ModeBar(QFrame):
    mode_requested = pyqtSignal(str)       # «normal» / «kana» / «vertical»
    layout_changed = pyqtSignal()          # cambió de tamaño o de visibilidad (quien la aloja puede recolocarla)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ModeBar")
        self.setStyleSheet(STYLE)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.ArrowCursor)
        self._mode = "normal"
        self._policy = "hover"
        self._available = False
        self._expanded = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.icon = QLabel("あ")
        lay.addWidget(self.icon)
        self.chip = QToolButton()
        self.chip.setObjectName("chip")
        self.chip.setFocusPolicy(Qt.NoFocus)
        self.chip.setCursor(Qt.PointingHandCursor)
        self.chip.clicked.connect(self._chip_clicked)
        lay.addWidget(self.chip)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for key, label, tip in MODES:
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self._clicked(k))
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[key] = b
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COLLAPSE_MS)
        self._timer.timeout.connect(self._collapse)
        self._refresh()

    # ------------------------------------------------------------------ API
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def policy(self) -> str:
        return self._policy

    @property
    def expanded(self) -> bool:
        return self._policy == "always" or self._expanded

    def set_available(self, available: bool):
        """Solo hay modos con el japonés."""
        if bool(available) != self._available:
            self._available = bool(available)
            self._refresh()

    def set_mode(self, mode: str):
        if mode in self.buttons and mode != self._mode:
            self._mode = mode
            self._refresh()
        elif mode in self.buttons:
            self._refresh()

    def set_policy(self, policy: str):
        self._policy = policy if policy in POLICIES else "hover"
        self._expanded = False
        self._timer.stop()
        self._refresh()

    def set_keys(self, keys: dict):
        """{«normal»: «1», …}: se añade a la ayuda de cada segmento."""
        for key, label, tip in MODES:
            hint = keys.get(f"mode_{key}", "") if keys else ""
            self.buttons[key].setToolTip(f"{tip}" + (f"  ·  tecla {hint}" if hint else ""))

    def is_shown(self) -> bool:
        return self._available and self._policy != "hidden"

    # ------------------------------------------------------------------ forma
    def _label(self, key: str) -> str:
        return next(label for k, label, _t in MODES if k == key)

    def _refresh(self):
        show = self.is_shown()
        body = show and self.expanded
        for key, b in self.buttons.items():
            b.setChecked(key == self._mode)
            b.setVisible(body)
        self.chip.setText(f"{self._label(self._mode)}  ▾")
        self.chip.setVisible(show and not body)
        self.icon.setVisible(show)
        self.setVisible(show)
        self.adjustSize()
        self.layout_changed.emit()

    def _clicked(self, key: str):
        if key != self._mode:
            self._mode = key
            self._refresh()
        self.mode_requested.emit(key)

    def _chip_clicked(self):
        if self._policy == "hover":
            self._expanded = True
            self._refresh()

    def _collapse(self):
        if self._policy == "hover" and self._expanded and not self.underMouse():
            self._expanded = False
            self._refresh()

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

    def sizeHint(self) -> QSize:
        return self.layout().sizeHint()
