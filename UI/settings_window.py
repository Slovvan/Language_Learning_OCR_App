"""Página «Configuración»: Atajos, Captura congelada, Modo en vivo, Apariencia y Perfiles. Los cambios se aplican al momento.

Es una página de la ventana principal (como Tarjetas, Registro y Glosario). Cada pestaña va en un área con desplazamiento
para no inflar el tamaño mínimo de la ventana, y las tarjetas pasan de dos columnas a una cuando falta ancho.
"""
from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                             QSlider, QStackedWidget, QVBoxLayout, QWidget)

import Translator.languages as languages
import UI.main_ui as main_ui
import UI.profiles as profiles
from UI.app_settings import (ACTIONS, KEY_SCOPES, NUMBERS, PANEL_MODES, default_bindings, default_keys, get_number, get_ocr_bar, load_bindings,
                          load_keys, panel_mode)
from UI.cards_window import ACCENT, MUTED, _Pill
from UI.hotkeys import (MAX_KEYS, global_key_token, global_label, is_risky, normalize_global, overlay_key_from_event)
from UI.live_options import _INFO
from UI.main_ui import confirm

AMBER = "#e0a548"

# (clave, título, ayuda, sufijo, paso)
LIVE_ROWS = (
    ("live_sensitivity", "Sensibilidad al cambio", "1 = solo cambios grandes · 10 = cualquier cambio.", "", 1),
    ("live_stable_ms", "Esperar a que se quede quieto", "Evita leer texto que aparece letra a letra.", " ms", 50),
    ("live_min_gap_ms", "Separación mínima entre lecturas", "Evita lecturas seguidas si el texto parpadea.", " ms", 100),
)
PANEL_ROWS = (
    ("live_history_max", "Lecturas en el historial", "Con «Historial» activado.", "", 5),
    ("live_panel_hide_s", "Duración del panel temporal", "Con «Panel temporal» activado.", " s", 1),
)
# acción -> (título, ayuda) de los atajos globales
HOTKEY_TEXT = {
    "capture": ("Capturar región", "Congela la pantalla y eliges la región (o la región fija)."),
    "read": ("Leer la región", "Modo en vivo: lee ahora."),
    "auto": ("Lectura automática", "Activar o desactivar."),
    "freeze": ("Congelar / descongelar", "La pantalla entera con la última lectura."),
    "text": ("Texto sobre el original", "Quitar o poner el texto dibujado."),
    "panel": ("Panel de traducción", "Abrir o cerrar."),
    "merge": ("Unir / separar líneas", "Para diálogos partidos."),
    "edit": ("Mover / estirar la región", "Cambia la región fija."),
}
SWITCH_TEXT = {
    "live_auto": ("Lectura automática", "Lee sola cuando el texto cambia y se queda quieto."),
    "live_freeze_read": ("Congelar al leer", "Tras «Leer» la pantalla queda congelada con el texto encima."),
    "live_freeze_auto": ("Congelar en automático", "Cada frase detectada congela la pantalla."),
    "live_pause_on": ("Pausar el juego al congelar", "Envía la tecla de pausa al congelar y otra vez al descongelar."),
    "live_show_original": ("Original en el panel", "Muestra también el texto reconocido."),
    "live_history": ("Historial", "Acumula las últimas lecturas, cada una con ✕."),
    "live_save_log": ("Guardar en el registro", "Cada lectura va a «Registro»."),
    "live_over": ("Traducción sobre la región", "Dibuja la traducción en vez del texto reconocido."),
    "live_panel_temp": ("Panel temporal", "Aparece al leer y se esconde solo."),
}
MODE_NAMES = {"normal": "OCR normal", "kana": "Solo kana", "vertical": "Vertical"}


# ---------------------------------------------------------------------------------------------------- piezas
class _KeyChips(QPushButton):
    """Un atajo dibujado como teclas (Ctrl · Alt · O). Al pulsarlo espera la combinación nueva. Las subclases dicen cómo."""
    started = pyqtSignal()
    WIDTH = 214
    placeholder = "Sin atajo"

    def __init__(self):
        super().__init__()
        self._value = ""
        self._waiting = False
        self.conflict = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.WIDTH, 36)
        self.setStyleSheet("QPushButton { background:transparent; border:none; padding:0; }")   # lo dibujamos nosotros

    def _tokens(self) -> list:
        raise NotImplementedError

    def sizeHint(self):
        return QSize(self.WIDTH, 36)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setBold(True)
        p.setFont(font)
        fm = QFontMetrics(font)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._waiting:
            pen = QPen(QColor("#38b6dc"), 1.4, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QColor("#0f2a3a"))
            p.drawRoundedRect(r, 9, 9)
            p.setPen(QColor("#6fd3ff"))
            p.drawText(r, Qt.AlignCenter, fm.elidedText(self.text(), Qt.ElideRight, int(r.width()) - 12))
            return
        tokens = self._tokens()
        if not tokens:
            p.setPen(QPen(QColor("#3a475c"), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            w = fm.horizontalAdvance(self.placeholder) + 28
            box = QRectF(r.right() - w, r.top(), w, r.height())
            p.drawRoundedRect(box, 9, 9)
            p.setPen(QColor("#6d7a8f"))
            p.drawText(box, Qt.AlignCenter, self.placeholder)
            return
        fg, bg, edge = (QColor(AMBER), QColor("#33270f"), QColor("#7a5a1c")) if self.conflict else \
            (QColor("#f0f5fc"), QColor("#202938"), QColor("#3a475c"))
        if self.hasFocus() and not self.conflict:
            edge = QColor("#38b6dc")
        widths = [max(34, fm.horizontalAdvance(t) + 24) for t in tokens]
        x = r.right() - (sum(widths) + 6 * (len(widths) - 1))
        for t, w in zip(tokens, widths):
            box = QRectF(x, r.top(), w, r.height())
            p.setPen(QPen(edge, 1))
            p.setBrush(bg)
            p.drawRoundedRect(box, 9, 9)
            p.setPen(fg)
            p.drawText(box, Qt.AlignCenter, t)
            x += w + 6

    def set_conflict(self, on: bool):
        if self.conflict != on:
            self.conflict = on
            self.update()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        if self._waiting:
            self._stop()
        super().focusOutEvent(e)
        self.update()

    def _start(self):
        self._waiting = True
        self.started.emit()
        self.grabKeyboard()
        self.setFocus()
        self._show()

    def _stop(self):
        self._waiting = False
        self.releaseKeyboard()
        self._show()

    def set_value(self, value: str):
        self._value = value
        self._waiting = False
        self.releaseKeyboard()
        self._show()

    def _show(self):
        self.update()


class SingleKeyButton(_KeyChips):
    """Una tecla (con o sin Ctrl/Alt/Mayús). Al pulsarlo espera la nueva. Esc cancela."""
    captured = pyqtSignal(str)
    placeholder = "Sin tecla"
    WIDTH = 132

    def __init__(self, value: str = ""):
        super().__init__()
        self._value = value
        self.clicked.connect(self._start)
        self._show()

    def _tokens(self):
        return [t for t in self._value.split("+") if t] if self._value else []

    def _show(self):
        self.setText("Pulsa la tecla…" if self._waiting else (self._value or "(sin tecla)"))
        self.update()

    def keyPressEvent(self, e):
        if not self._waiting:
            super().keyPressEvent(e)
            return
        if e.key() == Qt.Key_Escape and e.modifiers() == Qt.NoModifier:
            self._stop()                                    # Esc cancela (para dejar Esc como tecla, queda por defecto)
            return
        combo = overlay_key_from_event(e.modifiers(), e.key())
        if combo is None:                                   # solo un modificador: se sigue esperando
            return
        self._stop()
        self.captured.emit(combo)


class KeyCaptureButton(_KeyChips):
    """Un atajo global; al pulsarlo espera la combinación nueva (de 1 a 3 teclas) y se cierra al soltar todo."""
    captured = pyqtSignal(str)
    rejected = pyqtSignal(str)

    def __init__(self, value: str = ""):
        super().__init__()
        self._value = value
        self._down, self._seen = set(), []
        self.clicked.connect(self._start)
        self._show()

    @property
    def value(self) -> str:
        return self._value

    def _tokens(self):
        return global_label(self._value).split("+") if self._value else []

    def _show(self):
        self.setText("Pulsa la combinación…" if self._waiting else (global_label(self._value) or "(sin atajo)"))
        self.update()

    def _start(self):
        self._down, self._seen = set(), []
        super()._start()

    def keyPressEvent(self, e):
        if not self._waiting:
            super().keyPressEvent(e)
            return
        if e.key() == Qt.Key_Escape and not self._seen:
            self._stop()
            return
        if e.isAutoRepeat():
            return
        token = global_key_token(e.modifiers(), e.key())
        if token is not None:
            self._down.add(token)
            if token not in self._seen:
                self._seen.append(token)
            self.setText(" + ".join(global_label(t) for t in self._seen) + " …")
            self.update()

    def keyReleaseEvent(self, e):
        if not self._waiting:
            super().keyReleaseEvent(e)
            return
        if e.isAutoRepeat():
            return
        self._down.discard(global_key_token(e.modifiers(), e.key()))
        if self._down or not self._seen:
            return
        seen, self._seen = self._seen, []
        self._stop()
        if len(seen) > MAX_KEYS:
            self.rejected.emit(f"Un atajo puede tener como máximo {MAX_KEYS} teclas.")
            return
        combo = normalize_global("+".join(seen))
        if combo is None:
            self.rejected.emit("Esa combinación no sirve: necesita al menos una tecla que no sea solo Ctrl, Alt, Mayús o Win.")
            return
        self.captured.emit(combo)


class _Switch(QCheckBox):
    """Interruptor (en lugar de casilla). Se comporta como un QCheckBox: isChecked / setChecked / toggled."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QCheckBox { background:transparent; } QCheckBox::indicator { width:0; height:0; }")

    def sizeHint(self):
        return QSize(46, 26)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0b8fb8" if on else "#2a3345") if self.isEnabled() else QColor("#1c222e"))
        p.drawRoundedRect(QRectF(0, 1, 46, 24), 12, 12)
        p.setBrush(QColor("#ffffff" if on else "#8793a6"))
        p.drawEllipse(QPointF(34 if on else 12, 13), 9, 9)
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#6fd3ff"), 1.4))
            p.drawRoundedRect(QRectF(1, 2, 44, 22), 11, 11)


def _switch(model, key) -> _Switch:
    """Interruptor ligado a una opción del modo en vivo; sigue a los demás controles de la misma opción (como la tuerca del panel)."""
    box = _Switch()
    box.setToolTip(_INFO[key][2])
    box.setChecked(model.get(key))
    box.toggled.connect(lambda on, k=key: model.set(k, on))

    def follow(changed_key, value, b=box, k=key):
        if changed_key == k and b.isChecked() != value:
            b.blockSignals(True)
            b.setChecked(value)
            b.blockSignals(False)
            b.update()
    model.changed.connect(follow)
    return box


class _NumSlider(QSlider):
    """Deslizador que redondea al paso y escribe su valor (con unidad) en una etiqueta."""

    def __init__(self, key, suffix, step, value):
        super().__init__(Qt.Horizontal)
        _d, self.lo, self.hi = NUMBERS[key]
        self.step, self.suffix = step, suffix
        self.setRange(self.lo, self.hi)
        self.setSingleStep(step)
        self.setPageStep(step * 2 if step < 10 else step)
        self.label = QLabel()
        self.label.setObjectName("value")
        self.setValue(value)
        self._text(self.value())
        self.valueChanged.connect(self._snap)

    def _text(self, v):
        self.label.setText(f"{v}{self.suffix}")

    def _snap(self, v):
        snapped = max(self.lo, min(self.hi, int(round(v / self.step)) * self.step))
        if snapped != v:
            self.setValue(snapped)          # vuelve a emitir con el valor bueno; quien escuche debe leer value(), no el argumento
            return
        self._text(v)


class _Segmented(QFrame):
    """Selector de dos o tres opciones; misma interfaz básica que un QComboBox."""
    currentIndexChanged = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("seg")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self._buttons, self._data, self._index = [], [], 0

    def addItem(self, text, data=None):
        b = QPushButton(text)
        b.setObjectName("segbtn")
        b.setFocusPolicy(Qt.NoFocus)
        i = len(self._buttons)
        b.clicked.connect(lambda _c=False, n=i: self.setCurrentIndex(n))
        self.layout().addWidget(b)
        self._buttons.append(b)
        self._data.append(data)
        self._paint()

    def _paint(self):
        for i, b in enumerate(self._buttons):
            main_ui.set_prop(b, "on", i == self._index)

    def count(self):
        return len(self._buttons)

    def currentIndex(self):
        return self._index

    def currentData(self):
        return self._data[self._index] if self._data else None

    def setCurrentIndex(self, i):
        i = max(0, min(len(self._buttons) - 1, i))
        if i == self._index:
            self._paint()
            return
        self._index = i
        self._paint()
        self.currentIndexChanged.emit(i)


class _Card(QFrame):
    """Tarjeta con un título en versalitas y filas."""

    def __init__(self, title=""):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 16, 20, 14)
        self.lay.setSpacing(0)
        if title:
            head = QLabel(title.upper())
            head.setObjectName("sect")
            self.lay.addWidget(head)
            self.lay.addSpacing(4)
        self._rows = []

    def intro(self, text):
        lab = QLabel(text)
        lab.setObjectName("intro")
        lab.setWordWrap(True)
        lab.setContentsMargins(0, 0, 0, 4)
        self.lay.addWidget(lab)
        return lab

    def add(self, row):
        self.lay.addWidget(row)
        self._rows.append(row)
        for r in self._rows:                                 # solo la última fila va sin línea debajo
            main_ui.set_prop(r, "last", r is row)
        return row


def _row(title, help_="", *controls, warn=False):
    """Fila: título y ayuda a la izquierda, controles a la derecha. Con `warn` lleva una línea de aviso (oculta) bajo la ayuda."""
    w = QWidget()
    w.setObjectName("row")
    w.setAttribute(Qt.WA_StyledBackground, True)
    grid = QGridLayout(w)
    grid.setContentsMargins(0, 10, 0, 10)
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(2)
    t = QLabel(title)
    t.setObjectName("rowtitle")
    t.setWordWrap(True)
    grid.addWidget(t, 0, 0)
    w.title_label = t
    line = 1
    if help_:
        h = QLabel(help_)
        h.setObjectName("rowhelp")
        h.setWordWrap(True)
        grid.addWidget(h, line, 0)
        line += 1
    w.warn_label = None
    if warn:
        wl = QLabel()
        wl.setObjectName("rowwarn")
        wl.setWordWrap(True)
        wl.hide()
        grid.addWidget(wl, line, 0)
        w.warn_label = wl
        line += 1
    box = QHBoxLayout()
    box.setSpacing(6)
    for c in controls:
        box.addWidget(c, 0, Qt.AlignVCenter)
    grid.addLayout(box, 0, 1, max(line, 1), 1, Qt.AlignRight | Qt.AlignVCenter)
    grid.setColumnStretch(0, 1)
    return w


def _slider_row(title, help_, slider):
    """Fila con deslizador: título y valor arriba, ayuda y el deslizador debajo."""
    w = QWidget()
    w.setObjectName("row")
    w.setAttribute(Qt.WA_StyledBackground, True)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 10, 0, 12)
    lay.setSpacing(2)
    top = QHBoxLayout()
    t = QLabel(title)
    t.setObjectName("rowtitle")
    t.setWordWrap(True)
    top.addWidget(t, 1)
    top.addWidget(slider.label)
    lay.addLayout(top)
    if help_:
        h = QLabel(help_)
        h.setObjectName("rowhelp")
        h.setWordWrap(True)
        lay.addWidget(h)
    lay.addSpacing(4)
    lay.addWidget(slider)
    return w


def _icon_button(text, tip):
    b = QPushButton(text)
    b.setObjectName("iconbtn")
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.NoFocus)
    b.setCursor(Qt.PointingHandCursor)
    return b


class _Columns(QWidget):
    """Dos columnas de tarjetas que pasan a una sola cuando falta ancho."""
    TWO = 700

    def __init__(self, left, right):
        super().__init__()
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        self._cols = []
        for cards in (left, right):
            col = QWidget()
            lay = QVBoxLayout(col)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(16)
            for c in cards:
                lay.addWidget(c)
            lay.addStretch(1)
            self._cols.append(col)
        self._two = None
        self._place(True)

    def _place(self, two):
        if two == self._two:
            return
        self._two = two
        for col in self._cols:
            self._grid.removeWidget(col)
        self._grid.addWidget(self._cols[0], 0, 0)
        self._grid.addWidget(self._cols[1], 0, 1) if two else self._grid.addWidget(self._cols[1], 1, 0)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1 if two else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place(self.width() >= self.TWO)


class _CardGrid(QWidget):
    """Reparte tarjetas en 3, 2 o 1 columnas según el ancho."""

    def __init__(self):
        super().__init__()
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(16)
        self._widgets, self._n = [], 0

    def set_widgets(self, widgets):
        for w in self._widgets:
            self._grid.removeWidget(w)
            w.hide()
            w.setParent(None)
            w.deleteLater()
        self._widgets, self._n = list(widgets), 0
        self._layout()

    def _layout(self):
        n = 3 if self.width() >= 760 else 2 if self.width() >= 500 else 1
        if n == self._n and all(self._grid.indexOf(w) >= 0 for w in self._widgets):
            return
        self._n = n
        for w in self._widgets:
            self._grid.removeWidget(w)
        for i, w in enumerate(self._widgets):
            self._grid.addWidget(w, i // n, i % n)
            w.show()
        for c in range(3):
            self._grid.setColumnStretch(c, 1 if c < n else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()


class _Tabs(QWidget):
    """Pestañas como selector segmentado. Misma interfaz que lo usado de un QTabWidget (addTab, tabText, widget...)."""
    currentChanged = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._seg = QFrame()
        self._seg.setObjectName("seg")
        self._seg_lay = QHBoxLayout(self._seg)
        self._seg_lay.setContentsMargins(4, 4, 4, 4)
        self._seg_lay.setSpacing(4)
        self._stack = QStackedWidget()
        self._buttons = []
        top = QHBoxLayout()
        top.addWidget(self._seg)
        top.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addLayout(top)
        lay.addWidget(self._stack, 1)

    def addTab(self, widget, text):
        b = QPushButton(text)
        b.setObjectName("segbtn")
        b.setFocusPolicy(Qt.NoFocus)
        i = len(self._buttons)
        b.clicked.connect(lambda _c=False, n=i: self.setCurrentIndex(n))
        self._seg_lay.addWidget(b)
        self._buttons.append(b)
        self._stack.addWidget(widget)
        main_ui.set_prop(b, "on", i == 0)

    def count(self):
        return len(self._buttons)

    def tabText(self, i):
        return self._buttons[i].text()

    def widget(self, i):
        return self._stack.widget(i)

    def currentIndex(self):
        return self._stack.currentIndex()

    def setCurrentIndex(self, i):
        i = max(0, min(self.count() - 1, i))
        self._stack.setCurrentIndex(i)
        for n, b in enumerate(self._buttons):
            main_ui.set_prop(b, "on", n == i)
        self.currentChanged.emit(i)


class _DimPreview(QWidget):
    """Miniatura de la pantalla congelada: todo oscurecido salvo la región, con el texto leído."""

    def __init__(self):
        super().__init__()
        self.pct = 47
        self.setFixedHeight(132)
        self.setMinimumWidth(180)

    def set_percent(self, pct):
        self.pct = pct
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        clip = r.adjusted(0, 0, 0, 0)
        grad = QLinearGradient(clip.topLeft(), clip.bottomRight())
        grad.setColorAt(0, QColor("#8fb0d8"))
        grad.setColorAt(1, QColor("#d9c8a3"))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(clip, 10, 10)
        p.setBrush(QColor(255, 255, 255, 60))                  # unas formas para que se note lo que se oscurece
        p.drawRoundedRect(QRectF(clip.left() + 14, clip.top() + 14, 46, 30), 6, 6)
        p.drawRoundedRect(QRectF(clip.right() - 70, clip.bottom() - 40, 56, 28), 6, 6)
        hole = QRectF(clip.center().x() - 70, clip.center().y() - 20, 140, 40)
        from PyQt5.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(clip, 10, 10)
        hole_path = QPainterPath()
        hole_path.addRect(hole)
        p.setBrush(QColor(0, 0, 0, round(255 * self.pct / 100)))
        p.drawPath(path.subtracted(hole_path))
        p.setBrush(QColor("#3b465b"))
        p.setPen(QPen(QColor("#6fd3ff"), 1.5))
        p.drawRect(hole)
        font = QFont(self.font())
        font.setPixelSize(17)
        p.setFont(font)
        p.setPen(QColor("#f2f6fc"))
        p.drawText(hole, Qt.AlignCenter, "宿屋で休もう。")


class _LookPreview(_Card):
    """Vista previa de Apariencia: el panel con su tamaño de letra y la ventanita del diccionario con su escala."""

    def __init__(self):
        super().__init__("Vista previa")
        self.panel = QLabel()
        self.panel.setWordWrap(True)
        self.word = QLabel()
        self.meaning = QLabel()
        self.meaning.setWordWrap(True)
        self.pills = QWidget()
        pl = QHBoxLayout(self.pills)
        pl.setContentsMargins(0, 4, 0, 0)
        pl.setSpacing(8)
        pl.addWidget(_Pill("JLPT N3", "jlpt"))
        pl.addWidget(_Pill("Juegos #2 540", "freq"))
        pl.addStretch(1)
        cap1, cap2 = QLabel("PANEL"), QLabel("DICCIONARIO")
        for c in (cap1, cap2):
            c.setObjectName("sect")
        self.lay.addSpacing(6)
        self.lay.addWidget(cap1)
        self.lay.addSpacing(6)
        self.lay.addWidget(self.panel)
        self.lay.addSpacing(14)
        self.lay.addWidget(cap2)
        self.lay.addSpacing(6)
        self.lay.addWidget(self.word)
        self.lay.addWidget(self.meaning)
        self.lay.addWidget(self.pills)
        self.set_values(16, 100)

    def set_values(self, panel_px, scale):
        self.panel.setText(f'<span style="font-size:12px; color:#8793a6;">経験値を獲得した。</span><br>'
                           f'<span style="font-size:{panel_px}px; color:#f2f6fc;">Obtuviste puntos de experiencia.</span>')
        px = max(14, round(30 * scale / 100))
        self.word.setText(f'<span style="font-size:{px}px; font-weight:800; color:#ffffff;">経験値</span> '
                          f'<span style="font-size:{max(11, round(15 * scale / 100))}px; color:{ACCENT};">けいけんち</span>')
        self.meaning.setText(f'<span style="font-size:{max(12, round(15 * scale / 100))}px; font-weight:700; color:#f2f6fc;">'
                             f'1. experiencia (puntos)</span>')


# ---------------------------------------------------------------------------------------------------- página
class SettingsPage(QWidget):
    changed = pyqtSignal()               # algo cambió: la ventana principal lo aplica
    profile_use = pyqtSignal(str)        # «Usar» un perfil
    profile_save = pyqtSignal(str)       # «Guardar la configuración actual» con ese nombre
    profiles_changed = pyqtSignal()      # se borró o renombró un perfil

    def __init__(self, settings, live_options, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.live_options = live_options
        self.setWindowTitle("Configuración")
        self._buttons = {}               # acción global -> KeyCaptureButton
        self._rows = {}                  # ("hk"|"frozen"|"live", acción) -> fila
        self._clear_buttons = {}
        self._key_buttons = {}
        self._spins = {}                 # clave numérica -> deslizador
        self._checkboxes = {}
        self._flagged = []

        title = QLabel("Configuración")
        title.setObjectName("h1")
        sub = QLabel("Los cambios se aplican al momento")
        sub.setObjectName("muted")
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(sub)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setMinimumHeight(20)
        self.tabs = _Tabs()
        self.tabs.addTab(self._hotkeys_tab(), "Atajos")
        self.tabs.addTab(self._frozen_tab(), "Captura congelada")
        self.tabs.addTab(self._live_tab(), "Modo en vivo")
        self.tabs.addTab(self._look_tab(), "Apariencia")
        self.tabs.addTab(self._profiles_tab(), "Perfiles")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 12)
        layout.setSpacing(12)
        layout.addLayout(head)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.note)
        self.tabs.currentChanged.connect(lambda _i: (self._unflag(), self._say("")))
        self._say("")

    # ------------------------------------------------------------------ utilidades
    def _say(self, text: str, error: bool = False):
        self.note.setStyleSheet(f"color:{AMBER}; font-size:12px;" if error else f"color:{MUTED}; font-size:12px;")
        self.note.setText(text)

    @staticmethod
    def _page(*blocks):
        """Pestaña: contenido en un área con desplazamiento (no aumenta el tamaño mínimo de la ventana)."""
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 8, 6)
        lay.setSpacing(16)
        for b in blocks:
            lay.addWidget(b) if isinstance(b, QWidget) else lay.addLayout(b)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        return scroll

    @staticmethod
    def _footer(*buttons):
        row = QHBoxLayout()
        for b in buttons:
            row.addWidget(b)
        row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        return wrap

    def _slider(self, key, suffix, step):
        s = _NumSlider(key, suffix, step, get_number(self.settings, key))
        s.valueChanged.connect(lambda _v, k=key, sl=s: self._set_number(k, sl.value()))
        self._spins[key] = s
        return s

    def _set_number(self, key, value):
        self.settings.setValue(key, int(value))
        self.changed.emit()

    def _slider_rows(self, card, rows):
        for key, title, help_, suffix, step in rows:
            card.add(_slider_row(title, help_, self._slider(key, suffix, step)))

    def _reset_numbers(self, keys):
        for key in keys:
            self._spins[key].setValue(NUMBERS[key][0])        # dispara valueChanged y guarda
        self._say("Valores restablecidos.")

    def _flag(self, kind, action, text):
        """Pinta la fila de ámbar con el aviso debajo."""
        self._unflag()
        row = self._rows.get((kind, action))
        btn = self._buttons.get(action) if kind == "hk" else self._key_buttons.get((kind, action))
        if kind == "pause":
            btn = self._pause_btn
        if btn is not None:
            btn.set_conflict(True)
        if row is not None and row.warn_label is not None:
            row.warn_label.setText("⚠ " + text)
            row.warn_label.show()
        self._flagged.append((kind, action))

    def _unflag(self, *_):
        for kind, action in self._flagged:
            row = self._rows.get((kind, action))
            btn = self._buttons.get(action) if kind == "hk" else self._key_buttons.get((kind, action))
            if kind == "pause":
                btn = self._pause_btn
            if btn is not None:
                btn.set_conflict(False)
            if row is not None and row.warn_label is not None:
                row.warn_label.hide()
        self._flagged = []

    # ------------------------------------------------------------------ Atajos
    def _hotkeys_tab(self):
        card = _Card("Atajos globales")
        card.intro("Funcionan con el juego en primer plano. Haz clic en una combinación y pulsa las teclas nuevas "
                   "(de 1 a 3, por ejemplo F9, Ctrl+Mayús+K o Z+X). Se guarda al soltarlas. Esc cancela.")
        saved = load_bindings(self.settings)
        for action in ACTIONS:
            title, help_ = HOTKEY_TEXT[action]
            btn = KeyCaptureButton(saved.get(action, ""))
            btn.captured.connect(lambda combo, a=action: self._set_hotkey(a, combo))
            btn.rejected.connect(lambda why, a=action: (self._say(why, True), self._flag("hk", a, why)))
            btn.started.connect(self._unflag)
            clear = _icon_button("✕", "Quitar el atajo (dejar esta acción sin atajo)")
            clear.clicked.connect(lambda _c=False, a=action: self._set_hotkey(a, ""))
            reset = _icon_button("↺", "Volver al atajo de fábrica")
            reset.clicked.connect(lambda _c=False, a=action: self._set_hotkey(a, default_bindings()[a]))
            self._buttons[action] = btn
            self._clear_buttons[action] = clear
            self._rows[("hk", action)] = card.add(_row(title, help_, btn, clear, reset, warn=True))
        reset_all = QPushButton("Restablecer todos los atajos")
        reset_all.clicked.connect(self._reset_hotkeys)
        return self._page(card, self._footer(reset_all))

    def _set_hotkey(self, action, combo):
        self._unflag()
        if combo:
            for other, value in load_bindings(self.settings).items():
                if other != action and value == combo:
                    msg = f"«{global_label(combo)}» ya se usa para «{ACTIONS[other][1]}». Quita o cambia esa primero."
                    self._say(msg, True)
                    self._flag("hk", action, f"Ya la usa «{HOTKEY_TEXT[other][0]}». Quita o cambia esa primero.")
                    return
        self.settings.setValue(f"hk/{action}", combo)
        self._buttons[action].set_value(combo)
        if combo and is_risky(combo):
            self._say("Ojo: una tecla suelta también le llega al juego (los atajos no la bloquean). "
                      "Es más seguro usarla con Ctrl, Alt o Mayús.", True)
        else:
            self._say("Atajo guardado." if combo else "Acción sin atajo.")
        self.changed.emit()

    def _reset_hotkeys(self):
        self._unflag()
        for action, combo in default_bindings().items():
            self.settings.setValue(f"hk/{action}", combo)
            self._buttons[action].set_value(combo)
        self._say("Atajos restablecidos.")
        self.changed.emit()

    # --- teclas con el foco ---
    def _key_rows(self, card, scope):
        saved = load_keys(self.settings, scope)
        for action, (_d, name) in KEY_SCOPES[scope].items():
            btn = SingleKeyButton(saved.get(action, ""))
            btn.captured.connect(lambda combo, sc=scope, a=action: self._set_key(sc, a, combo))
            btn.started.connect(self._unflag)
            clear = _icon_button("✕", "Quitar la tecla")
            clear.clicked.connect(lambda _c=False, sc=scope, a=action: self._set_key(sc, a, ""))
            reset = _icon_button("↺", "Volver a la tecla de fábrica")
            reset.clicked.connect(lambda _c=False, sc=scope, a=action: self._set_key(sc, a, default_keys(sc)[a]))
            self._key_buttons[(scope, action)] = btn
            self._rows[(scope, action)] = card.add(_row(name, "", btn, clear, reset, warn=True))

    def _set_key(self, scope, action, combo):
        self._unflag()
        if combo:
            for other, value in load_keys(self.settings, scope).items():
                if other != action and value == combo:
                    self._say(f"«{combo}» ya se usa para «{KEY_SCOPES[scope][other][1]}». Quita o cambia esa primero.", True)
                    self._flag(scope, action, f"Ya la usa «{KEY_SCOPES[scope][other][1]}». Quita o cambia esa primero.")
                    return
        self.settings.setValue(f"key/{scope}/{action}", combo)
        self._key_buttons[(scope, action)].set_value(combo)
        self._say("Tecla guardada." if combo else "Acción sin tecla (los atajos globales siguen funcionando).")
        self.changed.emit()

    def _reset_keys(self, scope):
        self._unflag()
        for action, key in default_keys(scope).items():
            self.settings.setValue(f"key/{scope}/{action}", key)
            self._key_buttons[(scope, action)].set_value(key)
        self._say("Teclas restablecidas.")
        self.changed.emit()

    # ------------------------------------------------------------------ Captura congelada
    def _frozen_tab(self):
        keys = _Card("Teclas con la captura abierta")
        keys.intro("Se usan con la pantalla congelada en primer plano. Haz clic en una tecla y pulsa la nueva "
                   "(sola o con Ctrl, Alt o Mayús). Esc cancela.")
        self._key_rows(keys, "frozen")

        look = _Card("Aspecto al congelar")
        dim = _NumSlider("frozen_dim", " %", 1, get_number(self.settings, "frozen_dim"))
        dim.valueChanged.connect(lambda _v: self._set_number("frozen_dim", dim.value()))
        self._spins["frozen_dim"] = self._dim = dim
        look.add(_slider_row("Oscurecer lo que queda fuera de la región",
                             "Más oscuro resalta la región; menos deja ver el resto de la pantalla.", dim))
        self._dim_preview = _DimPreview()
        self._dim_preview.set_percent(dim.value())
        dim.valueChanged.connect(lambda _v: self._dim_preview.set_percent(dim.value()))
        look.lay.addSpacing(8)
        look.lay.addWidget(self._dim_preview)

        mouse = _Card("Con el mouse")
        text = QLabel("<b>Arrastrar</b>: elegir la región &nbsp;·&nbsp; <b>Clic derecho</b>: elegir otra región<br>"
                      "<span style='color:#8793a6'>El clic derecho no se puede cambiar.</span>")
        text.setWordWrap(True)
        mouse.lay.addSpacing(4)
        mouse.lay.addWidget(text)

        reset = QPushButton("Restablecer esta pestaña")
        reset.clicked.connect(self._reset_frozen)
        return self._page(_Columns([keys], [look, mouse]), self._footer(reset))

    def _reset_frozen(self):
        self._reset_keys("frozen")
        self._dim.setValue(NUMBERS["frozen_dim"][0])
        self._say("Valores restablecidos.")

    # ------------------------------------------------------------------ Modo en vivo
    def _switch_rows(self, card, keys):
        for key in keys:
            title, help_ = SWITCH_TEXT[key]
            sw = _switch(self.live_options, key)
            self._checkboxes[key] = sw
            card.add(_row(title, help_, sw))

    def _live_tab(self):
        read = _Card("Lectura")
        self._switch_rows(read, ("live_auto", "live_freeze_read", "live_freeze_auto", "live_pause_on"))
        self._pause_btn = KeyCaptureButton(str(self.settings.value("live_pause_key", "")))
        self._pause_btn.captured.connect(self._set_pause_key)
        self._pause_btn.rejected.connect(lambda why: (self._say(why, True), self._flag("pause", "", why)))
        self._pause_btn.started.connect(self._unflag)
        clear = _icon_button("✕", "Quitar la tecla de pausa")
        clear.clicked.connect(lambda: self._set_pause_key(""))
        self._rows[("pause", "")] = read.add(_row(
            "Tecla de pausa del juego",
            "La misma que usa tu juego (alterna pausa / reanuda). Se envía a la ventana en primer plano: "
            "usa los atajos globales para que el foco siga en el juego. Si el juego ya estaba en pausa al congelar, "
            "se reanudaría: en ese caso apaga «Pausar el juego».", self._pause_btn, clear, warn=True))

        panel = _Card("Panel de traducción")
        self._switch_rows(panel, ("live_show_original", "live_history", "live_save_log", "live_over", "live_panel_temp"))

        detect = _Card("Detección del cambio")
        self._slider_rows(detect, LIVE_ROWS)
        numbers = _Card("Panel · números")
        self._slider_rows(numbers, PANEL_ROWS)
        tip = _Card("Atajo útil")
        tip.intro("Las mismas casillas de «Lectura» y «Panel» están en la tuerca ⚙ del panel, "
                  "para cambiarlas sin abrir esta pantalla.")

        keys = _Card("Teclas con la región o el panel en foco")
        keys.intro("Haz clic en la región o el panel para darle el foco; desde el juego usa los atajos globales.")
        self._key_rows(keys, "live")

        reset = QPushButton("Restablecer esta pestaña")
        reset.setToolTip("Vuelve a los valores de fábrica los números y las teclas de esta pestaña")
        reset.clicked.connect(lambda: (self._reset_numbers([r[0] for r in LIVE_ROWS + PANEL_ROWS]), self._reset_keys("live")))
        return self._page(_Columns([read, panel], [detect, numbers, tip]), keys, self._footer(reset))

    def _set_pause_key(self, combo):
        self._unflag()
        self.settings.setValue("live_pause_key", combo)
        self._pause_btn.set_value(combo)
        if combo and is_risky(combo):
            self._say("Tecla de pausa guardada. Ojo: es una tecla suelta; si el juego la usa para otra cosa, se activará igual.", True)
        else:
            self._say("Tecla de pausa guardada." if combo else "Sin tecla de pausa: «Pausar el juego» no hará nada.")
        self.changed.emit()

    def reload_numbers(self):
        """Un número cambió desde fuera (A− / A+ del menú ⚙ del panel): los deslizadores lo siguen sin volver a guardar."""
        for key, slider in self._spins.items():
            value = get_number(self.settings, key)
            if slider.value() != value:
                slider.blockSignals(True)
                slider.setValue(value)
                slider.blockSignals(False)
                slider._text(value)
        preview = getattr(self, "_preview", None)
        if preview is not None and "live_panel_font" in self._spins and "popup_scale" in self._spins:
            preview.set_values(self._spins["live_panel_font"].value(), self._spins["popup_scale"].value())

    def reload_pause_key(self):
        """La tecla de pausa cambió desde fuera (al aplicar un perfil)."""
        self._pause_btn.set_value(str(self.settings.value("live_pause_key", "")))

    # ------------------------------------------------------------------ Apariencia
    def _look_tab(self):
        card = _Card("Panel y texto")
        style = _Segmented()
        style.addItem("Completo", "full")
        style.addItem("Mínimo", "minimal")
        style.addItem("Viñeta", "tab")
        style.setCurrentIndex(max(0, PANEL_MODES.index(panel_mode(self.settings))))

        def save_mode(_i):
            mode = style.currentData()
            self.settings.setValue("live_panel_mode", mode)
            self.settings.setValue("live_panel_tab", mode == "tab")          # (lo que guardaban las versiones anteriores)
            self.changed.emit()
        style.currentIndexChanged.connect(save_mode)
        self._panel_style = style
        card.add(_row("Panel de traducción",
                      "Completo: historial y botones siempre a la vista. Mínimo: solo la traducción; los botones salen al pasar "
                      "el mouse. Viñeta: una pestaña que abre el panel al pasar el mouse.", style))

        bar = QComboBox()
        bar.addItem("Viñeta al pasar el mouse", "hover")
        bar.addItem("Siempre visible", "always")
        bar.addItem("Oculta (solo con 1 / 2 / 3)", "hidden")
        bar.setMinimumWidth(190)
        bar.setCurrentIndex(max(0, bar.findData(get_ocr_bar(self.settings))))
        bar.currentIndexChanged.connect(lambda _i: (self.settings.setValue("ocr_bar", bar.currentData()), self.changed.emit()))
        self._ocr_bar = bar
        card.add(_row("Barra de modos OCR (japonés)", "Cómo se muestra «あ Modo ▾».", bar))

        font = self._slider("live_panel_font", " px", 1)
        self._font_slider = font
        card.add(_slider_row("Tamaño de letra del panel", "En píxeles.", font))
        self._opacity = opacity = self._slider("live_text_opacity", " %", 5)
        card.add(_slider_row("Opacidad del fondo del texto", "Menos opacidad deja ver el original a través del texto dibujado.", opacity))
        self._popup_scale = scale = self._slider("popup_scale", " %", 5)
        card.add(_slider_row("Letra del diccionario", "Ventanita de palabras (70–200 %).", scale))

        self._preview = _LookPreview()
        self._preview.set_values(font.value(), scale.value())
        font.valueChanged.connect(lambda _v: self._preview.set_values(font.value(), scale.value()))
        scale.valueChanged.connect(lambda _v: self._preview.set_values(font.value(), scale.value()))

        reset = QPushButton("Restablecer esta sección")

        def do_reset():
            font.setValue(NUMBERS["live_panel_font"][0])
            style.setCurrentIndex(0)
            bar.setCurrentIndex(0)
            opacity.setValue(NUMBERS["live_text_opacity"][0])
            scale.setValue(NUMBERS["popup_scale"][0])
            self._say("Valores restablecidos.")
        reset.clicked.connect(do_reset)
        return self._page(_Columns([card], [self._preview]), self._footer(reset))

    # ------------------------------------------------------------------ Perfiles
    def _profiles_tab(self):
        intro = QLabel("Un perfil recuerda, para cada juego: la región fija, el idioma, el modo de OCR, las opciones del modo en vivo "
                       "y la tecla de pausa. También puedes elegirlo desde la ventana principal (selector «Perfil»).")
        intro.setObjectName("intro")
        intro.setWordWrap(True)
        self._profile_grid = _CardGrid()
        self._profile_empty = QLabel("Aún no hay perfiles. Guarda abajo la configuración que tienes ahora.")
        self._profile_empty.setObjectName("empty")
        self._profile_empty.setAlignment(Qt.AlignCenter)
        self._profile_empty.setMinimumHeight(70)

        save = _Card("Guardar la configuración actual")
        self.profile_name = QLineEdit()
        self.profile_name.setPlaceholderText("Nombre del juego o perfil")
        self.btn_p_save = QPushButton("Guardar como perfil nuevo")
        self.btn_p_save.setProperty("variant", "primary")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.profile_name, 1)
        row.addWidget(self.btn_p_save)
        save.lay.addSpacing(6)
        save.lay.addLayout(row)
        hint = QLabel("Si el nombre ya existe, te preguntará si lo reemplazas con lo que tienes ahora.")
        hint.setObjectName("intro")
        hint.setContentsMargins(0, 8, 0, 0)
        save.lay.addWidget(hint)
        self.btn_p_save.clicked.connect(self._profile_save)
        self.profile_name.returnPressed.connect(self._profile_save)
        page = self._page(intro, self._profile_empty, self._profile_grid, save)
        self.reload_profiles()
        return page

    def profile_names(self) -> list:
        return list(profiles.load(self.settings))

    def _profile_card(self, name, data):
        active = str(self.settings.value("profile_active", "")) == name
        card = QFrame()
        card.setObjectName("card")
        card.setProperty("active", "true" if active else "false")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(5)
        top = QHBoxLayout()
        label = QLabel(name)
        label.setObjectName("cardname")
        label.setWordWrap(True)
        top.addWidget(label, 1)
        if active:
            top.addWidget(_Pill("EN USO", "use"), 0, Qt.AlignTop)
        lay.addLayout(top)
        bits = []
        if data.get("lang"):
            pack = languages.PACKS.get(data["lang"])
            bits.append(pack.source_name if pack else data["lang"])
        if data.get("mode"):
            bits.append(MODE_NAMES.get(data["mode"], data["mode"]))
        lines = [" · ".join(bits)] if bits else []
        lines.append(f"Región {data['rect'][2]} × {data['rect'][3]}" if "rect" in data else "Sin región")
        n = sum(1 for v in (data.get("options") or {}).values() if v)
        if "options" in data:
            lines.append(f"{n} opción activa" if n == 1 else f"{n} opciones activas")
        for text in lines:
            lab = QLabel(text)
            lab.setObjectName("rowhelp")
            lab.setWordWrap(True)
            lay.addWidget(lab)
        key = (data.get("pause") or {}).get("key", "")
        pause = QHBoxLayout()
        pause.setSpacing(8)
        cap = QLabel("Tecla de pausa")
        cap.setObjectName("rowhelp")
        pause.addWidget(cap)
        pause.addWidget(_Pill(global_label(key) if key else "—", "key"))
        pause.addStretch(1)
        lay.addLayout(pause)
        lay.addStretch(1)                                    # los botones quedan alineados entre tarjetas de distinta altura
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        use = QPushButton("En uso" if active else "Usar")
        use.setEnabled(not active)
        if not active:
            use.setProperty("variant", "primary")
        use.setToolTip("Aplica idioma, modo de OCR, opciones, tecla de pausa y la región fija del perfil")
        use.clicked.connect(lambda _c=False, n=name: self._profile_use(n))
        ren = QPushButton("Renombrar")
        ren.clicked.connect(lambda _c=False, n=name: self._profile_rename(n))
        dele = QPushButton("Borrar")
        dele.setProperty("variant", "dangerghost")
        dele.clicked.connect(lambda _c=False, n=name: self._profile_delete(n))
        for b in (use, ren, dele):
            buttons.addWidget(b)
        lay.addLayout(buttons)
        card.name = name
        card.summary = " | ".join([name] + lines + ([f"pausa {global_label(key)}"] if key else []))
        return card

    def reload_profiles(self, select: str = None):
        data = profiles.load(self.settings)
        cards = [self._profile_card(name, d) for name, d in data.items()]
        self._profile_cards = {c.name: c for c in cards}
        self._profile_grid.set_widgets(cards)
        self._profile_grid.setVisible(bool(cards))
        self._profile_empty.setVisible(not cards)

    def profile_card_text(self, name) -> str:
        card = self._profile_cards.get(name)
        return card.summary if card else ""

    def _profile_use(self, name):
        if name in profiles.load(self.settings):
            self.profile_use.emit(name)
            self.reload_profiles()
            self._say(f"Perfil «{name}» aplicado.")

    def _profile_save(self):
        name = self.profile_name.text().strip()
        if not name:
            self._say("Escribe un nombre para el perfil.", True)
            self.profile_name.setFocus()
            return
        if name in profiles.load(self.settings) and not confirm(
                self, "Guardar perfil", f"Ya existe «{name}». ¿Reemplazarlo con la configuración actual?",
                yes_text="Reemplazar", no_text="Cancelar"):
            return
        self.profile_save.emit(name)
        self.profile_name.clear()
        self.reload_profiles()
        self._say(f"Perfil «{name}» guardado.")

    def _profile_rename(self, old):
        if old not in profiles.load(self.settings):
            return
        new, ok = main_ui.ask_text(self, "Renombrar perfil", "Nombre nuevo:", old)
        new = new.strip()
        if not ok or not new or new == old:
            return
        if not profiles.rename(self.settings, old, new):
            self._say(f"Ya existe un perfil llamado «{new}».", True)
            return
        if str(self.settings.value("profile_active", "")) == old:
            self.settings.setValue("profile_active", new)
        self.reload_profiles()
        self.profiles_changed.emit()
        self._say("Perfil renombrado.")

    def _profile_delete(self, name):
        if name not in profiles.load(self.settings) or not confirm(
                self, "Borrar perfil", f"¿Borrar el perfil «{name}»?", yes_text="Borrar", no_text="Cancelar", danger=True):
            return
        profiles.delete(self.settings, name)
        if str(self.settings.value("profile_active", "")) == name:
            self.settings.setValue("profile_active", "")
        self.reload_profiles()
        self.profiles_changed.emit()
        self._say("Perfil borrado.")


SettingsDialog = SettingsPage          # nombre de cuando era una ventana suelta
