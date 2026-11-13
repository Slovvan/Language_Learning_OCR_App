"""Aspecto de la ventana principal: barra lateral, tarjetas y botones (tema oscuro).

La hoja de estilo se aplica solo al contenedor «shell» de la ventana principal: las otras ventanas (tarjetas, registro,
glosario, configuración) son hijas de la principal pero no del contenedor, así que no la heredan.
"""
import os
import tempfile

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout

SHELL_QSS = """
QWidget#shell { background:#12141a; }
QWidget#shell QLabel { color:#c9d4e4; font-size:13px; background:transparent; }
QWidget#shell QLabel#brand { color:#ffffff; font-size:17px; font-weight:700; }
QWidget#shell QLabel#sect { color:#7fa3b8; font-size:11px; font-weight:700; letter-spacing:1px; }
QWidget#shell QLabel#muted { color:#7d8899; font-size:12px; }
QWidget#shell QLabel#status { color:#aab8cc; font-size:12px; }
QWidget#shell QLabel#warn { color:#f2d58a; background:#2a2415; border:1px solid #5c4a1c; border-radius:8px; padding:6px 10px; font-size:12px; }
QWidget#shell QFrame#side { background:#0e1015; border:none; border-right:1px solid #232b3a; }
QWidget#shell QFrame#card { background:#181c26; border:1px solid #2a3345; border-radius:12px; }
QWidget#shell QFrame#seg { background:#181c26; border:1px solid #2a3345; border-radius:12px; }
QWidget#shell QPushButton { color:#f2f2f2; background:rgba(255,255,255,16); border:1px solid rgba(255,255,255,45);
    border-radius:8px; padding:6px 12px; font-size:13px; }
QWidget#shell QPushButton:hover { background:rgba(255,255,255,40); }
QWidget#shell QPushButton:disabled { color:#5b6677; background:rgba(255,255,255,8); border-color:rgba(255,255,255,20); }
QWidget#shell QPushButton[variant="primary"] { background:#0b8fb8; border:1px solid #38b6dc; font-weight:600; font-size:14px; padding:9px 14px; }
QWidget#shell QPushButton[variant="primary"]:hover { background:#10a3cf; }
QWidget#shell QPushButton[variant="primary"]:disabled { background:#0d3f52; border-color:#1b5b73; color:#6f9bab; }
QWidget#shell QPushButton[variant="danger"] { color:#ffb4b4; }
QWidget#shell QPushButton[variant="danger"]:disabled { color:#6b4c4c; }
QWidget#shell QPushButton[on="true"] { background:#1d4a63; border-color:#6fd3ff; color:#ffffff; }
QWidget#shell QPushButton#segbtn { border:none; border-radius:8px; background:transparent; padding:6px 14px; }
QWidget#shell QPushButton#segbtn:hover { background:rgba(255,255,255,18); }
QWidget#shell QPushButton#segbtn[on="true"] { background:#1d4a63; border:1px solid #6fd3ff; }
QWidget#shell QPushButton#segbtn:disabled { color:#5b6677; }
QWidget#shell QPushButton#segbtn[on="true"]:disabled { color:#8fb9c8; background:#17384a; border-color:#3f7a94; }
QWidget#shell QPushButton#nav { background:transparent; border:none; border-radius:8px; padding:0; text-align:left; }
QWidget#shell QPushButton#nav:hover { background:#171d2a; }
QWidget#shell QPushButton#nav[current="true"] { background:#1d2a3d; }
QWidget#shell QPushButton#nav QLabel { color:#b8c4d6; font-size:14px; background:transparent; }
QWidget#shell QPushButton#nav[current="true"] QLabel { color:#ffffff; }
QWidget#shell QPushButton#nav QLabel#navbadge { background:#232b3a; color:#9fb4d4; border-radius:9px; padding:1px 8px; font-size:11px; font-weight:600; }
QWidget#shell QPushButton#nav QLabel#navbadge[warn="true"] { background:#4a3a14; color:#f2d58a; }
QWidget#shell QComboBox { color:#e8eef7; background:#1a1f2b; border:1px solid #2f3a4d; border-radius:8px; padding:6px 10px; font-size:13px; }
QWidget#shell QComboBox:disabled { color:#6b7688; }
QWidget#shell QComboBox QAbstractItemView { background:#1a1f2b; color:#e8eef7; selection-background-color:#1d4a63; }
QWidget#shell QComboBox::drop-down { border:none; width:22px; }
QWidget#shell QTextEdit { background:#0e1015; color:#d6f1ff; border:1px solid #2a3345; border-radius:10px; padding:6px; font-size:15px; }
QWidget#shell QCheckBox { color:#c9d4e4; font-size:13px; spacing:8px; background:transparent; }
QWidget#shell QCheckBox::indicator { width:16px; height:16px; border-radius:4px; border:1px solid #4a5a73; background:#0e1015; }
QWidget#shell QCheckBox::indicator:checked { background:#0b8fb8; border-color:#38b6dc; image:url("{CHECK}"); }
QWidget#shell QCheckBox::indicator:hover { border-color:#6fd3ff; }
QWidget#shell QPushButton#ghost { background:transparent; border:1px solid #2f3a4d; border-radius:7px; padding:2px 10px; font-size:12px; color:#9fb4d4; }
QWidget#shell QPushButton#ghost:hover { background:rgba(255,255,255,24); color:#ffffff; }
QWidget#shell QPushButton#ghost:disabled { color:#4a5566; border-color:#232b3a; }
QWidget#shell QLabel#h1 { color:#ffffff; font-size:22px; font-weight:700; }
QWidget#shell QLabel#ok { color:#7fd6a4; font-size:12px; }
QWidget#shell QLabel#empty { color:#c9d4e4; }
QWidget#shell QLineEdit { color:#e8eef7; background:#0e1015; border:1px solid #2a3345; border-radius:10px; padding:8px 12px; font-size:14px; selection-background-color:#1d4a63; }
QWidget#shell QLineEdit:focus { border-color:#38b6dc; }
QWidget#shell QLineEdit:disabled { color:#5b6677; background:#12151c; }
QWidget#shell QPlainTextEdit { background:#0e1015; color:#e8eef7; border:1px solid #2a3345; border-radius:10px; padding:6px 8px; font-size:15px; selection-background-color:#1d4a63; }
QWidget#shell QPlainTextEdit:focus { border-color:#38b6dc; }
QWidget#shell QPlainTextEdit:disabled { color:#5b6677; background:#12151c; }
QWidget#shell QListWidget#cardlist { background:transparent; border:none; outline:none; }
QWidget#shell QScrollArea { background:transparent; border:none; }
QWidget#shell QScrollArea > QWidget > QWidget { background:transparent; }
QWidget#shell QScrollBar:vertical { background:transparent; width:10px; margin:2px; }
QWidget#shell QScrollBar::handle:vertical { background:#2f3a4d; border-radius:4px; min-height:30px; }
QWidget#shell QScrollBar::handle:vertical:hover { background:#3d4b63; }
QWidget#shell QScrollBar::add-line:vertical, QWidget#shell QScrollBar::sub-line:vertical { height:0; }
QWidget#shell QScrollBar::add-page:vertical, QWidget#shell QScrollBar::sub-page:vertical { background:transparent; }
QWidget#shell QPushButton[variant="dangerghost"] { color:#ffb4b4; background:transparent; border:1px solid #5c3a3a; }
QWidget#shell QPushButton[variant="dangerghost"]:hover { background:rgba(255,120,120,28); }
QWidget#shell QPushButton[variant="dangerghost"]:disabled { color:#6b4c4c; border-color:#3a2a2a; }
QWidget#shell QPushButton#chip { background:transparent; border:1px solid #2f3a4d; border-radius:8px; padding:6px 14px; color:#b8c4d6; }
QWidget#shell QPushButton#chip:hover { background:rgba(255,255,255,18); color:#ffffff; }
QWidget#shell QPushButton#chip[on="true"] { background:#1d4a63; border-color:#6fd3ff; color:#ffffff; }
QWidget#shell QPushButton#chip:disabled { color:#4a5566; border-color:#232b3a; background:transparent; }
QWidget#shell QLabel#selcount { color:#6fd3ff; font-size:13px; font-weight:700; }
QWidget#shell QTableWidget { background:transparent; border:none; outline:none; color:#e8eef7; font-size:14px; }
QWidget#shell QTableWidget::item { padding:0 10px; border:none; border-bottom:1px solid #202737; }
QWidget#shell QTableWidget::item:hover { background:#1b2230; }
QWidget#shell QTableWidget::item:selected { background:#1b3a56; color:#ffffff; border-bottom:1px solid #284f72; }
QWidget#shell QHeaderView { background:transparent; border:none; }
QWidget#shell QHeaderView::section { background:transparent; color:#7fa3b8; font-size:11px; font-weight:700; letter-spacing:1px;
    border:none; border-bottom:1px solid #2a3345; padding:0 7px; }
QWidget#shell QTableCornerButton::section { background:transparent; border:none; }
QWidget#shell QLabel#preview { border:1px dashed #3a475c; border-radius:10px; color:#7d8899; }
QWidget#shell QWidget#row { border:none; border-bottom:1px solid #222a3a; background:transparent; }
QWidget#shell QWidget#row[last="true"] { border-bottom:none; }
QWidget#shell QLabel#rowtitle { color:#f2f6fc; font-size:14px; font-weight:700; }
QWidget#shell QLabel#rowhelp { color:#8793a6; font-size:12px; }
QWidget#shell QLabel#rowwarn { color:#e0a548; font-size:12px; }
QWidget#shell QLabel#value { color:#6fd3ff; font-size:13px; font-weight:700; }
QWidget#shell QLabel#intro { color:#8793a6; font-size:12px; }
QWidget#shell QLabel#cardname { color:#ffffff; font-size:18px; font-weight:700; }
QWidget#shell QPushButton#iconbtn { background:transparent; border:none; color:#8fb0c8; font-size:16px; padding:0; min-width:30px; max-width:30px; min-height:30px; max-height:30px; }
QWidget#shell QPushButton#iconbtn:hover { background:rgba(255,255,255,26); color:#ffffff; }
QWidget#shell QPushButton#iconbtn:disabled { color:#3f4a5c; background:transparent; }
QWidget#shell QFrame#card[active="true"] { border:1px solid #38b6dc; background:#16243a; }
QWidget#shell QSlider::groove:horizontal { height:4px; background:#2a3345; border-radius:2px; }
QWidget#shell QSlider::sub-page:horizontal { background:#0b8fb8; border-radius:2px; }
QWidget#shell QSlider::handle:horizontal { width:16px; height:16px; margin:-6px 0; border-radius:8px; background:#e8f4fb; }
QWidget#shell QSlider::handle:horizontal:hover { background:#ffffff; }
"""



def _check_icon() -> str:
    """Marca blanca de la casilla (PNG en la carpeta temporal: el estilo de Qt solo acepta archivos)."""
    path = os.path.join(tempfile.gettempdir(), "ocr_translator_check.png")
    if not os.path.exists(path):
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#ffffff"), 4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPointF(8, 17), QPointF(14, 23), QPointF(25, 9))
        p.end()
        pm.save(path, "PNG")
    return path.replace("\\", "/")


def build_qss() -> str:
    return SHELL_QSS.replace("{CHECK}", _check_icon())


def repolish(widget):
    """Vuelve a aplicar la hoja de estilo tras cambiar una propiedad dinámica (variant, on, current...)."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget, name: str, value) -> None:
    """Cambia una propiedad dinámica del estilo (a texto «true»/«false» para bool) y repinta solo si cambió."""
    value = ("true" if value else "false") if isinstance(value, bool) else value
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


class NavButton(QPushButton):
    """Entrada de la barra lateral: icono, nombre y, a la derecha, un contador opcional."""

    def __init__(self, icon: str, name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("nav")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(40)
        self.setFocusPolicy(Qt.TabFocus)
        self.setProperty("current", "false")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(10)
        self.icon_label = QLabel(icon)
        self.icon_label.setFixedWidth(18)
        self.name_label = QLabel(name)
        self.badge = QLabel("")
        self.badge.setObjectName("navbadge")
        self.badge.setProperty("warn", "false")
        self.badge.hide()
        for w in (self.icon_label, self.name_label, self.badge):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(self.icon_label)
        lay.addWidget(self.name_label, 1)
        lay.addWidget(self.badge)
        self.name = name

    def set_current(self, current: bool):
        set_prop(self, "current", current)

    def set_badge(self, text: str = "", warn: bool = False):
        """Contador a la derecha (vacío = sin contador). `warn`: color de aviso (palabras pendientes)."""
        self.badge.setText(text)
        self.badge.setVisible(bool(text))
        set_prop(self.badge, "warn", warn)


class StatusLabel(QLabel):
    """Estado de la app con un punto de color: verde (listo), ámbar (trabajando) o rojo (algo falló)."""
    COLORS = {"ok": "#4cc38a", "busy": "#e8b84a", "bad": "#ee6b6b"}
    BUSY = ("iniciando", "cargando", "procesando", "traduciendo", "leyendo", "cambiando")
    BAD = ("error", "no se pudo", "falta", "no está listo", "no disponible", "dañado", "no encontrado")

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setContentsMargins(16, 0, 0, 0)
        self.setMinimumHeight(34)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._tone = "busy"
        self.setText(text)

    @staticmethod
    def tone_of(text: str) -> str:
        low = text.lower()
        if any(k in low for k in StatusLabel.BAD):
            return "bad"
        if any(k in low for k in StatusLabel.BUSY):
            return "busy"
        return "ok"

    def setText(self, text):
        self._tone = self.tone_of(str(text))
        super().setText(text)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.COLORS[self._tone]))
        line = self.fontMetrics().height()
        p.drawEllipse(QPointF(4, line / 2 + 1), 3.5, 3.5)
        p.end()


DIALOG_QSS = """
QMessageBox { background:#161c27; }
QMessageBox QLabel { color:#e8eef7; font-size:13px; background:transparent; min-width:280px; }
QMessageBox QPushButton { color:#e8eef7; background:#222b3a; border:1px solid #33405a; border-radius:8px; padding:7px 18px; min-width:84px; }
QMessageBox QPushButton:hover { background:#2b3750; }
QMessageBox QPushButton:default { background:#1b8fb5; border-color:#38b6dc; color:#ffffff; }
QMessageBox QPushButton[danger="true"] { background:#a83a3a; border-color:#c75a5a; color:#ffffff; }
QMessageBox QPushButton[danger="true"]:hover { background:#bd4545; }
"""


PROMPT_QSS = """
QDialog { background:#161c27; }
QLabel { color:#e8eef7; font-size:13px; background:transparent; }
QLineEdit { color:#e8eef7; background:#0e1015; border:1px solid #2a3345; border-radius:8px; padding:7px 10px; font-size:14px; selection-background-color:#1d4a63; }
QLineEdit:focus { border-color:#38b6dc; }
QPushButton { color:#e8eef7; background:#222b3a; border:1px solid #33405a; border-radius:8px; padding:7px 18px; min-width:84px; }
QPushButton:hover { background:#2b3750; }
QPushButton:default { background:#1b8fb5; border-color:#38b6dc; color:#ffffff; }
"""


def confirm(parent, title: str, text: str, yes_text="Aceptar", no_text="Cancelar", danger=False) -> bool:
    """Pregunta de sí/no con los colores de la app (el cuadro estándar heredaba el estilo oscuro y salía en blanco).
    Con `danger` el botón de aceptar es rojo y la opción segura (cancelar) queda como predeterminada."""
    box = QMessageBox(QMessageBox.NoIcon, title, text, QMessageBox.NoButton, parent.window() if parent is not None else None)
    box.setStyleSheet(DIALOG_QSS)
    yes = box.addButton(yes_text, QMessageBox.AcceptRole)
    no = box.addButton(no_text, QMessageBox.RejectRole)
    if danger:
        set_prop(yes, "danger", "true")
    box.setDefaultButton(no if danger else yes)
    box.setEscapeButton(no)
    box.exec_()
    return box.clickedButton() is yes


def ask_text(parent, title: str, label: str, text: str = ""):
    """Pide un texto con los colores de la app. Devuelve (texto, aceptado), igual que QInputDialog.getText."""
    dlg = QDialog(parent.window() if parent is not None else None, Qt.Dialog | Qt.WindowCloseButtonHint)
    dlg.setWindowTitle(title)
    dlg.setStyleSheet(PROMPT_QSS)
    dlg.setMinimumWidth(360)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(18, 16, 18, 14)
    lay.setSpacing(10)
    lay.addWidget(QLabel(label))
    edit = QLineEdit(text)
    edit.selectAll()
    lay.addWidget(edit)
    row = QHBoxLayout()
    row.addStretch(1)
    no, yes = QPushButton("Cancelar"), QPushButton("Aceptar")
    yes.setDefault(True)
    no.clicked.connect(dlg.reject)
    yes.clicked.connect(dlg.accept)
    edit.returnPressed.connect(dlg.accept)
    row.addWidget(no)
    row.addWidget(yes)
    lay.addLayout(row)
    accepted = dlg.exec_() == QDialog.Accepted
    return edit.text(), accepted
