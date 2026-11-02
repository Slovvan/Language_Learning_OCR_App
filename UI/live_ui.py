"""Ventanas del modo en vivo: el marco de la región fija y el panel con el texto y la traducción.

Ninguna de las dos le quita el foco al juego, y el marco deja pasar los clics (el juego los recibe).
"""
import sys

from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QTextDocument
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QSizeGrip, QTextEdit,
                             QToolButton, QVBoxLayout, QWidget)

from UI.overlay_toolbar import OverlayToolbar
from UI.word_popup import HoverTextEdit, iter_words

FRAME_PAD = 3          # el marco se dibuja fuera de la región para no salir en lo que se lee
GAP = 8                # separación entre la región y el panel
MIN_W, MAX_W = 420, 900
MIN_PANEL_W, MIN_PANEL_H = 260, 110
EDIT_PAD = 8           # margen de agarre del marco al editar la región
MIN_REGION = 24        # tamaño mínimo (px lógicos) de la región al cambiarla de tamaño


def compute_panel_rect(region: QRect, size: QSize, screen: QRect, side: str = "auto") -> QRect:
    """Dónde poner el panel (px lógicos globales). `side`: auto / below / above / left / right.
    En «auto» se prueba debajo, encima, a la derecha y a la izquierda, y se queda con el primero que cabe entero
    en la pantalla; si ninguno cabe, se pega al borde por el lado con más espacio."""
    w, h = size.width(), size.height()

    def at(s):
        if s == "below":
            return QRect(region.left(), region.bottom() + 1 + GAP, w, h)
        if s == "above":
            return QRect(region.left(), region.top() - GAP - h, w, h)
        if s == "right":
            return QRect(region.right() + 1 + GAP, region.top(), w, h)
        return QRect(region.left() - GAP - w, region.top(), w, h)

    def fits(r):
        return screen.contains(r)

    order = ["below", "above", "right", "left"] if side == "auto" else [side]
    chosen = next((at(s) for s in order if fits(at(s))), None)
    if chosen is None:
        if side == "auto":                                  # el lado con más espacio
            room = {"below": screen.bottom() - region.bottom(), "above": region.top() - screen.top(),
                    "right": screen.right() - region.right(), "left": region.left() - screen.left()}
            chosen = at(max(room, key=room.get))
        else:
            chosen = at(side)
    x = max(screen.left(), min(chosen.left(), screen.right() - w + 1))      # siempre dentro de la pantalla
    y = max(screen.top(), min(chosen.top(), screen.bottom() - h + 1))
    return QRect(x, y, w, h)


class RegionFrame(QWidget):
    """Marco fino alrededor de la región fija. Normalmente transparente al mouse y sin foco; en modo edición
    se puede arrastrar (mover) y estirar por los bordes y esquinas. Emite `region_edited` al soltar."""
    region_edited = pyqtSignal(QRect)
    edit_finished = pyqtSignal()           # doble clic dentro: terminar la edición

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.color = QColor(0, 200, 255, 200)
        self._rect = QRect()               # la región (px lógicos globales)
        self._editable = False
        self._drag = None                  # (zona, posición global inicial, región inicial)
        self.bounds = None                 # QRect máximo (la pantalla) dentro del que se mueve la región

    @property
    def editable(self) -> bool:
        return self._editable

    @property
    def region(self) -> QRect:
        return QRect(self._rect)

    def _pad(self) -> int:
        return EDIT_PAD if self._editable else FRAME_PAD

    def set_region(self, region: QRect):
        self._rect = QRect(region)
        pad = self._pad()
        self.setGeometry(self._rect.adjusted(-pad, -pad, pad, pad))

    def set_editable(self, editable: bool):
        if editable == self._editable:
            return
        self._editable = editable
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowTransparentForInput, not editable)
        self.setCursor(Qt.ArrowCursor)
        self.set_region(self._rect)
        if was_visible:
            self.show()
        self.update()

    def set_color(self, color: QColor):
        self.color = color
        self.update()

    # ------------------------------------------------------------------ edición
    def _zone(self, pos: QPoint) -> str:
        """Zona del mouse: «move», o los lados que se estiran («l», «tr»…)."""
        inner = QRect(EDIT_PAD, EDIT_PAD, self._rect.width(), self._rect.height())
        m = EDIT_PAD
        near_l, near_r = abs(pos.x() - inner.left()) <= m, abs(pos.x() - inner.right()) <= m
        near_t, near_b = abs(pos.y() - inner.top()) <= m, abs(pos.y() - inner.bottom()) <= m
        inside = inner.adjusted(-m, -m, m, m).contains(pos)
        if not inside:
            return ""
        zone = ("t" if near_t else "b" if near_b else "") + ("l" if near_l else "r" if near_r else "")
        return zone or "move"

    CURSORS = {"move": Qt.SizeAllCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor,
               "b": Qt.SizeVerCursor, "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
               "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}

    def mousePressEvent(self, e):
        if not self._editable or e.button() != Qt.LeftButton:
            return
        zone = self._zone(e.pos())
        if zone:
            self._drag = (zone, e.globalPos(), QRect(self._rect))

    def mouseMoveEvent(self, e):
        if not self._editable:
            return
        if self._drag is None:
            self.setCursor(self.CURSORS.get(self._zone(e.pos()), Qt.ArrowCursor))
            return
        zone, start, orig = self._drag
        self.set_region(resize_rect(orig, zone, e.globalPos() - start, self.bounds))

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.region_edited.emit(self.region)

    def mouseDoubleClickEvent(self, e):
        if self._editable and self._zone(e.pos()) == "move":
            self.edit_finished.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        if self._editable:
            inner = QRect(EDIT_PAD, EDIT_PAD, self._rect.width(), self._rect.height())
            p.fillRect(inner, QColor(0, 200, 255, 40))          # casi invisible, pero recibe el mouse
            p.setPen(QPen(QColor(255, 255, 255, 235), 2))
            p.drawRect(inner)
            p.setBrush(QColor(0, 200, 255, 255))
            for pt in (inner.topLeft(), inner.topRight(), inner.bottomLeft(), inner.bottomRight(),
                       QPoint(inner.center().x(), inner.top()), QPoint(inner.center().x(), inner.bottom()),
                       QPoint(inner.left(), inner.center().y()), QPoint(inner.right(), inner.center().y())):
                p.drawRect(QRect(pt.x() - 4, pt.y() - 4, 8, 8))
        else:
            p.setPen(QPen(self.color, 2, Qt.DashLine))
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        p.end()


def resize_rect(orig: QRect, zone: str, delta: QPoint, bounds: QRect = None) -> QRect:
    """`orig` movido o estirado según la zona agarrada y lo que se arrastró; nunca menor que MIN_REGION
    ni fuera de `bounds`."""
    x0, y0, x1, y1 = orig.left(), orig.top(), orig.right() + 1, orig.bottom() + 1
    dx, dy = delta.x(), delta.y()
    if zone == "move":
        w, h = x1 - x0, y1 - y0
        x0, y0 = x0 + dx, y0 + dy
        if bounds is not None:
            x0 = max(bounds.left(), min(x0, bounds.right() + 1 - w))
            y0 = max(bounds.top(), min(y0, bounds.bottom() + 1 - h))
        return QRect(x0, y0, w, h)
    if "l" in zone:
        x0 = min(x0 + dx, x1 - MIN_REGION)
    if "r" in zone:
        x1 = max(x1 + dx, x0 + MIN_REGION)
    if "t" in zone:
        y0 = min(y0 + dy, y1 - MIN_REGION)
    if "b" in zone:
        y1 = max(y1 + dy, y0 + MIN_REGION)
    if bounds is not None:
        x0, y0 = max(bounds.left(), x0), max(bounds.top(), y0)
        x1, y1 = min(bounds.right() + 1, x1), min(bounds.bottom() + 1, y1)
    return QRect(x0, y0, x1 - x0, y1 - y0)


def exclude_from_capture(widget) -> bool:
    """Que la ventana no salga en las capturas de pantalla (Windows 10 2004 o superior). Así el texto dibujado
    encima de la región no lo lee el propio modo en vivo. Devuelve False si no se pudo."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        WDA_EXCLUDEFROMCAPTURE = 0x11
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE))
    except Exception:  # noqa: BLE001
        return False


class _LayerLine:
    def __init__(self, text, offset, rect, bg, fg, font, words):
        self.text, self.offset, self.rect, self.bg, self.fg, self.font, self.words = text, offset, rect, bg, fg, font, words


class LiveLayer(QWidget):
    """Texto dibujado ENCIMA de la región fija, como en la captura congelada, pero sin congelar el juego.

    Normalmente deja pasar los clics (el juego los recibe) y no toma el foco. Con `set_interactive(True)` recibe el
    mouse: al dejarlo quieto sobre una palabra sale su significado y un clic la guarda. Se excluye de las capturas
    para que el modo en vivo no lea su propio texto."""
    TRANSLATION, ORIGINAL = "translation", "original"
    word_context = pyqtSignal(str, int)
    word_hovered = pyqtSignal(str, str, QPoint)
    hover_ended = pyqtSignal()
    wheel_moved = pyqtSignal(int)
    word_clicked = pyqtSignal(str, str, int, str)     # palabra, compuesto, posición, texto completo

    def __init__(self, exclude_fn=exclude_from_capture, hover_delay_ms: int = 350):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._exclude_fn = exclude_fn
        self.safe = False                  # ¿la exclusión de la captura funciona? sin ella no se dibuja nada
        self.content = self.TRANSLATION
        self._bg, self._fg = QColor(20, 20, 24), QColor(245, 245, 245)
        self._text = ""                    # traducción
        self._lines = []                   # [_LayerLine] del original
        self._enabled = True               # el usuario lo apaga con el atajo / ajuste
        self._has_content = False
        self._suppressed = False           # oculto un momento (la región cambia, se edita, congelado…)
        self._force = False                # visible aunque esté «suprimido» (pantalla congelada)
        self._interactive = False
        self._marks, self._pending = set(), set()
        self._hover_key = None
        self._hover_pending = None
        self._hover_shown = False
        self._press = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(hover_delay_ms)
        self._timer.timeout.connect(self._emit_hover)

    # ------------------------------------------------------------------ vida
    def prepare(self) -> bool:
        """Con la ventana ya creada: comprueba que se pueda excluir de la captura."""
        self.show()
        self.safe = bool(self._exclude_fn(self))
        self.hide()
        return self.safe

    def set_region(self, region: QRect):
        self.setGeometry(region)

    def set_colors(self, bg: QColor, fg: QColor):
        self._bg, self._fg = bg, fg

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self._lines)

    def set_translation(self, text: str):
        self._text = text
        self._lines = []
        self._end_hover()
        self._has_content = bool(text.strip())
        self._refresh()

    def set_lines(self, entries):
        """entries: [(texto, QRectF en coordenadas de la región, fondo, color de texto)]."""
        from overlay import uniform_font
        self._end_hover()
        self._text = ""
        font = uniform_font([(t, r) for t, r, _b, _f in entries], self.font())
        fm = QFontMetrics(font)
        self._lines, offset = [], 0
        for text, rect, bg, fg in entries:
            width = fm.horizontalAdvance(text)
            wide = rect.adjusted(0, 0, max(0.0, width - rect.width()), 0)      # si se pasa, el parche la cubre
            words = [(s, e, w, c, rect.left() + fm.horizontalAdvance(text[:s]), rect.left() + fm.horizontalAdvance(text[:e]))
                     for s, e, w, c in iter_words(text)]
            self._lines.append(_LayerLine(text, offset, wide.adjusted(-3, -2, 3, 2), bg, fg, font, words))
            self._lines[-1].text_rect = rect
            offset += len(text) + 1
        self._has_content = bool(self._lines)
        self._refresh()

    def set_marks(self, ranges, pending=()):
        self._marks, self._pending = set(ranges), set(pending)
        self.update()

    def clear(self):
        self._end_hover()
        self._text, self._lines, self._has_content = "", [], False
        self._refresh()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self._refresh()

    def set_suppressed(self, suppressed: bool):
        self._suppressed = bool(suppressed)
        self._refresh()

    def set_forced(self, forced: bool):
        """Pantalla congelada: la capa se muestra siempre que haya texto."""
        self._force = bool(forced)
        self._refresh()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def interactive(self) -> bool:
        return self._interactive

    def set_interactive(self, on: bool):
        if on == self._interactive:
            return
        self._interactive = on
        self._end_hover()
        was = self.isVisible()
        self.setWindowFlag(Qt.WindowTransparentForInput, not on)
        self.setCursor(Qt.ArrowCursor)
        if was:
            self.show()
            self.raise_()

    def wanted(self) -> bool:
        return self.safe and self._enabled and self._has_content and (self._force or not self._suppressed)

    def _refresh(self):
        if self.wanted():
            self.show()
            self.raise_()
        else:
            self._end_hover()
            self.hide()
        self.update()

    # ------------------------------------------------------------------ palabras
    def _hit(self, pos):
        for li, line in enumerate(self._lines):
            if line.rect.contains(QPointF(pos)):
                for wi, w in enumerate(line.words):
                    if w[4] - 1 <= pos.x() <= w[5] + 1:
                        return li, wi
        return None

    def mouseMoveEvent(self, e):
        if not self._interactive:
            return
        hit = self._hit(e.pos())
        if hit == self._hover_key:
            return
        self._end_hover()
        self._hover_key = hit
        if hit:
            line = self._lines[hit[0]]
            w = line.words[hit[1]]
            self._hover_pending = (line.text, w[0], w[2], w[3], e.globalPos())
            self._timer.start()
        self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
        self.update()

    def _emit_hover(self):
        if self._hover_pending is None:
            return
        text, start, word, compound, pos = self._hover_pending
        self._hover_shown = True
        self.word_context.emit(text, start)
        self.word_hovered.emit(word, compound, pos)

    def _end_hover(self):
        self._timer.stop()
        self._hover_key = self._hover_pending = None
        if self._hover_shown:
            self._hover_shown = False
            self.hover_ended.emit()

    def leaveEvent(self, e):
        self._end_hover()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        if self._hover_shown:
            self.wheel_moved.emit(e.angleDelta().y())

    def mousePressEvent(self, e):
        self._end_hover()
        self._press = e.pos() if e.button() == Qt.LeftButton else None

    def mouseReleaseEvent(self, e):
        press, self._press = self._press, None
        if press is None or (e.pos() - press).manhattanLength() > 4:
            return
        hit = self._hit(e.pos())
        if hit:
            line = self._lines[hit[0]]
            w = line.words[hit[1]]
            self.word_clicked.emit(w[2], w[3], line.offset + w[0], self.text)

    # ------------------------------------------------------------------ dibujo
    def paintEvent(self, _):
        if not self.wanted():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        if self.content == self.ORIGINAL and self._lines:
            for li, line in enumerate(self._lines):
                p.fillRect(line.rect, line.bg)
                fm = QFontMetrics(line.font)
                for wi, w in enumerate(line.words):
                    span = QRectF(w[4] - 1, line.rect.top() + 1, w[5] - w[4] + 2, line.rect.height() - 2)
                    key = (line.offset + w[0], line.offset + w[1])
                    if key in self._marks:
                        p.fillRect(span, QColor(46, 190, 80, 150))
                    elif key in self._pending:
                        p.fillRect(span, QColor(240, 150, 30, 150))
                    if self._hover_key == (li, wi):
                        p.fillRect(span, QColor(255, 255, 255, 70))
                        p.setPen(QPen(line.fg, 1))
                        y = span.bottom() - 1
                        p.drawLine(QPointF(span.left(), y), QPointF(span.right(), y))
                p.setFont(line.font)
                p.setPen(line.fg)
                rect = line.text_rect
                baseline = rect.center().y() + (fm.ascent() - fm.descent()) / 2
                p.drawText(QPointF(rect.left(), baseline), line.text)
        elif self._text:
            area = QRectF(self.rect())
            p.fillRect(area, QColor(self._bg.red(), self._bg.green(), self._bg.blue(), 245))
            inner = area.adjusted(10, 6, -10, -6)
            doc = QTextDocument()
            font = QFont(self.font())
            size = max(10, min(40, int(inner.height() * 0.5)))
            while True:                       # la letra más grande que cabe en la región
                font.setPixelSize(size)
                doc.setDefaultFont(font)
                doc.setTextWidth(inner.width())
                doc.setPlainText(self._text)
                if size <= 10 or doc.size().height() <= inner.height():
                    break
                size -= 1
            p.translate(inner.left(), inner.top() + max(0.0, (inner.height() - doc.size().height()) / 2))
            from PyQt5.QtGui import QAbstractTextDocumentLayout
            ctx = QAbstractTextDocumentLayout.PaintContext()
            ctx.palette.setColor(ctx.palette.Text, self._fg)
            doc.documentLayout().draw(p, ctx)
        p.end()


class FreezeScreen(QWidget):
    """Pantalla congelada del modo en vivo: solo la imagen de la pantalla, a pantalla completa. El texto, el panel y
    el marco del modo en vivo se quedan por encima y siguen funcionando. Esc, clic derecho o el mismo botón la quitan."""
    closed = pyqtSignal()

    def __init__(self, close_key_fn=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._pixmap = None
        self._close_key = close_key_fn or (lambda: "Esc")
        self.setFocusPolicy(Qt.StrongFocus)

    def open(self, pixmap, geometry: QRect):
        self._pixmap = pixmap
        self.setGeometry(geometry)
        self.show()
        self.activateWindow()
        self.setFocus()

    def close_freeze(self):
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def keyPressEvent(self, e):
        from hotkeys import overlay_matches
        if e.key() == Qt.Key_Escape or overlay_matches(self._close_key(), e.modifiers(), e.key()):
            self.close_freeze()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.close_freeze()

    def paintEvent(self, _):
        if self._pixmap is not None:
            p = QPainter(self)
            p.drawPixmap(self.rect(), self._pixmap)
            p.end()


STATE_COLOR = {"watching": QColor(0, 200, 255, 200), "changing": QColor(240, 190, 30, 220),
               "reading": QColor(46, 190, 80, 230), "paused": QColor(150, 150, 150, 200)}
STATE_TEXT = {"watching": "Vigilando", "changing": "Cambiando…", "reading": "Leyendo…", "paused": "En pausa"}

PANEL_STYLE = """
LivePanel { background: rgb(18, 20, 26); border: 1px solid rgba(255,255,255,70); border-radius: 8px; }
QLabel#head { color: #9aa3b2; font-size: 12px; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
LiveEntry { background: rgba(255,255,255,12); border-radius: 6px; }
LiveEntry:hover { background: rgba(255,255,255,22); }
QTextEdit { background: transparent; border: none; color: #f2f2f2; }
QLabel#tr { color: #b9e6ff; }
QToolButton#del { color: #c9cdd6; background: transparent; border: none; font-size: 14px; padding: 0 4px; }
QToolButton#del:hover { color: #ff7b7b; }
"""


class LiveEntry(QFrame):
    """Una lectura del historial: original (con diccionario al pasar el mouse), traducción y ✕ para quitarla."""
    remove_requested = pyqtSignal(object)

    def __init__(self, text: str, log_id=None):
        super().__init__()
        self.log_id = log_id
        self.original = HoverTextEdit()
        self.original.setReadOnly(True)
        self.original.setFrameShape(QFrame.NoFrame)
        self.original.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.original.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.original.setPlainText(text)
        self.translation = QLabel("")
        self.translation.setObjectName("tr")
        self.translation.setWordWrap(True)
        self.translation.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.delete = QToolButton()
        self.delete.setObjectName("del")
        self.delete.setText("✕")
        self.delete.setToolTip("Quitar esta lectura (también del registro de frases)")
        self.delete.setFocusPolicy(Qt.NoFocus)
        self.delete.clicked.connect(lambda _c=False: self.remove_requested.emit(self))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 4, 6)
        lay.setSpacing(2)
        lay.addWidget(self.original)
        bottom = QWidget()
        from PyQt5.QtWidgets import QHBoxLayout
        row = QHBoxLayout(bottom)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.translation, 1)
        row.addWidget(self.delete, 0, Qt.AlignTop)
        lay.addWidget(bottom)

    def set_font(self, font: QFont):
        self.original.setFont(font)
        self.original.document().setDefaultFont(font)
        self.translation.setFont(font)
        self.fit()

    def set_translation(self, text: str):
        self.translation.setText(text)
        self.fit()

    def show_original(self, show: bool):
        self.original.setVisible(show)

    def fit(self):
        """Alto del original según su contenido (así no hace falta una barra de desplazamiento por lectura)."""
        width = max(100, self.original.viewport().width() or (self.width() - 40))
        self.original.document().setTextWidth(width)
        self.original.setFixedHeight(int(self.original.document().size().height()) + 8)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()


class LivePanel(QFrame):
    """Panel acoplado junto a la región: historial de lecturas (original con diccionario, traducción y ✕ para
    quitar una), más controles. No roba el foco; se arrastra desde la franja de arriba.
    En modo «última lectura» solo conserva la más reciente."""
    action_requested = pyqtSignal(str)       # read / freeze / edit / pause / clear / stop (de la barra)
    mode_requested = pyqtSignal(str)
    merge_toggled = pyqtSignal()
    settings_requested = pyqtSignal()
    entry_removed = pyqtSignal(object, bool)   # id del registro (o None), ¿era la más reciente?
    # eco de las señales de cada original, para conectarlas una sola vez
    word_context = pyqtSignal(str, int)
    word_hovered = pyqtSignal(str, str, QPoint)
    hover_ended = pyqtSignal()
    wheel_moved = pyqtSignal(int)
    word_clicked = pyqtSignal(str, str, int, str)   # palabra, compuesto, posición, texto de la lectura

    def __init__(self, settings=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self._placing = True                 # hasta que esté montado, los cambios de tamaño no son del usuario
        self.setObjectName("LivePanel")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(PANEL_STYLE)
        self._settings = settings
        self._region = QRect()
        self._side = "auto"
        self._moved = False                  # el usuario lo arrastró: no se recoloca solo
        self._drag = None
        self._hidden_by_user = False
        self.entries = []
        self.head = QLabel("En vivo")
        self.head.setObjectName("head")
        self.head.setCursor(Qt.SizeAllCursor)
        self.toolbar = OverlayToolbar(self)
        self.toolbar.set_tab_text("⚙ Controles")
        self.toolbar.add_action("read", "Leer ahora", "Lee la región en este momento")
        self.toolbar.add_action("freeze", "Congelar", "Congela la pantalla un momento con el texto encima")
        self.toolbar.add_action("edit", "Mover región", "Arrastra el marco para moverla o estirarla", checkable=True)
        self.toolbar.add_action("interact", "Palabras", "Deja el mouse sobre el texto de la región para ver el significado "
                                "y guardar palabras (mientras está activo, los clics no llegan al juego)", checkable=True)
        self.toolbar.add_action("pause", "Pausa", "Deja de leer sin cerrar la región", checkable=True)
        self.toolbar.add_action("clear", "Vaciar", "Vacía el panel (el registro de frases no se toca)")
        self.toolbar.add_action("stop", "Detener", "Termina el modo en vivo")
        self.toolbar.action_requested.connect(self.action_requested)
        self.toolbar.mode_requested.connect(self.mode_requested)
        self.toolbar.merge_toggled.connect(self.merge_toggled)
        self.toolbar.settings_requested.connect(self.settings_requested)
        self._blank = LiveEntry("")          # para que `original` / `translation` existan aunque no haya lecturas
        self.holder = QWidget()
        self.list_layout = QVBoxLayout(self.holder)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setFocusPolicy(Qt.NoFocus)
        self.scroll.setWidget(self.holder)
        self.setMinimumSize(MIN_PANEL_W, MIN_PANEL_H)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(4)
        lay.addWidget(self.head)
        lay.addWidget(self.toolbar, 0, Qt.AlignLeft)
        lay.addWidget(self.scroll, 1)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        self.grip = QSizeGrip(self)
        self.grip.setToolTip("Arrastra para cambiar el tamaño del panel")
        grip_row.addWidget(self.grip)
        lay.addLayout(grip_row)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_size)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)
        self.apply_settings()
        self._placing = False
        if settings is not None:
            settings.changed.connect(self._on_setting)

    # ------------------------------------------------------------------ ajustes
    def _get(self, key, default):
        return self._settings.get(key) if self._settings is not None else default

    def _on_setting(self, key):
        if key == "live.panel_show":
            self._hidden_by_user = self._get("live.panel_show", "always") == "hidden"
            if self._hidden_by_user:
                self.hide()
            elif self._region.isValid() and self.entries_active:
                self.place()
                self.show()
        if key in ("live.panel_w", "live.panel_h"):
            return
        if key.startswith("live.panel") or key in ("ui.toolbar", "live.log_max"):
            self.apply_settings()
            self._trim()
            if self._region.isValid() and self.isVisible():
                self.place()

    entries_active = True                  # lo pone LiveMode: el modo en vivo está en marcha

    @property
    def log_mode(self) -> bool:
        return self._get("live.panel_mode", "log") == "log"

    def apply_settings(self):
        size = int(self._get("live.panel_font", 15))
        self._font = QFont(self.font())
        self._font.setPixelSize(size)
        for entry in self.entries + [self._blank]:
            entry.set_font(self._font)
        self._show_original = bool(self._get("live.panel_original", False))
        for entry in self.entries + [self._blank]:
            entry.show_original(self._show_original)
        self.setWindowOpacity(int(self._get("live.panel_opacity", 92)) / 100.0)
        self._side = str(self._get("live.panel_side", "auto"))
        self.toolbar.set_policy(str(self._get("ui.toolbar", "hover")))
        if self._side != "auto":
            self._moved = False

    def panel_size(self) -> QSize:
        font = int(self._get("live.panel_font", 15))
        uw, uh = int(self._get("live.panel_w", 0)), int(self._get("live.panel_h", 0))
        if uw >= MIN_PANEL_W and uh >= MIN_PANEL_H:               # el usuario ya le dio un tamaño
            return QSize(uw, uh)
        w = max(MIN_W, min(MAX_W, self._region.width() if self._region.isValid() else MIN_W))
        rows = 3.0 if self.log_mode else (2.0 if self._show_original else 1.2)
        return QSize(w, int(font * 5.2 * rows + 110))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.isVisible() and not self._placing and self._settings is not None:
            self._save_timer.start()                                    # lo cambió el usuario con la esquina

    def _save_size(self):
        self._settings.set("live.panel_w", self.width())
        self._settings.set("live.panel_h", self.height())

    def reset_size(self):
        self._settings.reset("live.panel_w")
        self._settings.reset("live.panel_h")
        self.place()

    # ------------------------------------------------------------------ posición
    def set_region(self, region: QRect):
        self._region = QRect(region)
        self._moved = False
        self.place()

    def place(self):
        if not self._region.isValid() or self._moved:
            return
        screen = QApplication.screenAt(self._region.center()) or QApplication.primaryScreen()
        geo = screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self._placing = True
        try:
            self.setGeometry(compute_panel_rect(self._region, self.panel_size(), geo, self._side))
        finally:
            self._placing = False

    # ------------------------------------------------------------------ contenido
    @property
    def original(self):
        return (self.entries[-1] if self.entries else self._blank).original

    @property
    def translation(self):
        return (self.entries[-1] if self.entries else self._blank).translation

    def add_entry(self, text: str, log_id=None) -> LiveEntry:
        if not self.log_mode:
            for old in list(self.entries):
                self._drop(old)
        entry = LiveEntry(text, log_id)
        entry.set_font(self._font)
        entry.show_original(self._show_original)
        entry.remove_requested.connect(self._remove)
        o = entry.original
        o.word_context.connect(self.word_context)
        o.word_hovered.connect(self.word_hovered)
        o.hover_ended.connect(self.hover_ended)
        o.wheel_moved.connect(self.wheel_moved)
        o.word_clicked.connect(lambda w, c, s, e=entry: self.word_clicked.emit(w, c, s, e.original.toPlainText()))
        self.list_layout.insertWidget(self.list_layout.count() - 1, entry)
        self.entries.append(entry)
        self._trim()
        self._touch()
        QTimer.singleShot(0, self._scroll_end)
        return entry

    def _scroll_end(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _trim(self):
        limit = int(self._get("live.log_max", 30))
        while len(self.entries) > limit:
            self._drop(self.entries[0])

    def _drop(self, entry):
        if entry in self.entries:
            self.entries.remove(entry)
        entry.hide()
        self.list_layout.removeWidget(entry)
        entry.deleteLater()

    def _remove(self, entry):
        latest = bool(self.entries) and entry is self.entries[-1]
        log_id = entry.log_id
        self._drop(entry)
        self.entry_removed.emit(log_id, latest)

    def set_log_id(self, log_id):
        if self.entries:
            self.entries[-1].log_id = log_id

    def set_translation(self, text: str):
        if self.entries:
            self.entries[-1].set_translation(text)
            QTimer.singleShot(0, self._scroll_end)
        self._touch()

    def clear_entries(self):
        for entry in list(self.entries):
            self._drop(entry)

    def original_texts(self) -> list:
        return [e.original.toPlainText() for e in self.entries]

    def refresh_marks(self, words):
        for e in self.entries:
            text = e.original.toPlainText()
            e.original.set_marks(words.marks(text), words.pending_marks(text))

    def set_state(self, state: str):
        self.head.setText(f"En vivo · {STATE_TEXT.get(state, state)}")
        self.toolbar.extra["pause"].setChecked(state == "paused")

    def set_head_note(self, note: str):
        self.head.setText(note)

    def _touch(self):
        """Hay texto nuevo: se muestra el panel (salvo que esté oculto) y se reinicia el temporizador de ocultar."""
        if not self._hidden_by_user and self._region.isValid() and self.entries_active:
            self.show()
        self._restart_hide()

    def _restart_hide(self):
        secs = int(self._get("live.panel_hide_s", 0))
        self._hide_timer.stop()
        if secs > 0:
            self._hide_timer.start(secs * 1000)

    def _auto_hide(self):
        if not self.underMouse():
            self.hide()
        else:
            self._hide_timer.start(1500)

    def toggle_visible(self) -> bool:
        """Atajo del panel: lo esconde o lo devuelve. Devuelve si queda visible."""
        if self.isVisible():
            self._hidden_by_user = True
            self.hide()
        else:
            self._hidden_by_user = False
            if self._region.isValid():
                self.place()
                self.show()
                self._restart_hide()
        return self.isVisible()

    def start(self, region: QRect):
        self.entries_active = True
        self._hidden_by_user = self._get("live.panel_show", "always") == "hidden"
        self.set_region(region)
        self.clear_entries()
        self.apply_settings()
        if not self._hidden_by_user:
            self.show()

    def stop(self):
        self.entries_active = False
        self._hide_timer.stop()
        self.hide()

    # ------------------------------------------------------------------ arrastre
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.head.geometry().contains(e.pos()):
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPos() - self._drag)
            self._moved = True
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):
        self._hide_timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._restart_hide()
        super().leaveEvent(e)
