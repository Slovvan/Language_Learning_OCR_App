"""Modo en vivo (versión mínima): eliges la región UNA vez y la lees cuando tú quieras.

  · Elegir región: Ctrl+Alt+O (sobre la captura congelada, como siempre) y queda fija.
  · Leer: botón del panel, de la ventana principal o Ctrl+Alt+R. No hay detección automática.
  · La región es una ventana encima del juego que SIEMPRE recibe el mouse: tras leer, el texto reconocido se dibuja
    sobre el original (como en la captura congelada) y se puede pasar el mouse por las palabras (significado) y
    hacer clic para guardarlas. Los clics sobre la región no llegan al juego.
  · El panel muestra la última traducción. Se arrastra por la franja de arriba y se estira por la esquina.
  · Mismas teclas que la captura congelada, con la región o el panel en foco (haz clic en ellos): T muestra/oculta la
    traducción (el panel), O muestra/oculta el texto sobre el original, M une/separa líneas, Esc detiene el modo en
    vivo, E mueve/estira la región y clic derecho sobre la región elige otra. Desde el juego: Ctrl+Alt+T, Ctrl+Alt+H
    (texto), Ctrl+Alt+M, Ctrl+Alt+E.

Lectura automática (opcional, A / Ctrl+Alt+A): se vigila la región y se lee cuando el texto cambia y se queda quieto
(el que aparece letra a letra no se lee a medias). Mientras tanto el texto dibujado se quita al cambiar la pantalla y
vuelve con la lectura nueva. Para poder ver el juego por debajo, la región se excluye de las capturas de pantalla
(Windows 10 versión 2004 o superior); si no se puede, solo está la lectura manual.

Para leer a mano, la región y el panel se vuelven invisibles un instante (así no se lee el propio texto) y se capturan solo esos píxeles.
"""
from PyQt5.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizeGrip, QTextEdit, QWidget,
                             QVBoxLayout)

from UI.capture import bgr_to_qpixmap, grab_region
from UI.live import LiveController, signature
from UI.live_ui_helpers import exclude_from_capture
from UI.overlay import MARK, PENDING, sample_colors, uniform_font
from UI.word_popup import iter_words

READ_DELAY_MS = 150            # lo que se espera a que la región y el panel desaparezcan antes de capturar
GAP = 8                        # separación entre la región y el panel
GRAB = 8                       # margen (px) para agarrar los bordes de la región al editarla
MIN_REGION = 24                # tamaño mínimo (px lógicos) de la región
PANEL_MIN = QSize(260, 110)


def compute_panel_rect(region: QRect, size: QSize, screen: QRect) -> QRect:
    """Dónde poner el panel: debajo de la región, o encima, a la derecha o a la izquierda, lo primero que quepa
    entero en la pantalla (si nada cabe, junto al borde con más espacio). Siempre dentro de la pantalla."""
    w, h = size.width(), size.height()
    spots = {"below": QRect(region.left(), region.bottom() + 1 + GAP, w, h),
             "above": QRect(region.left(), region.top() - GAP - h, w, h),
             "right": QRect(region.right() + 1 + GAP, region.top(), w, h),
             "left": QRect(region.left() - GAP - w, region.top(), w, h)}
    chosen = next((r for r in spots.values() if screen.contains(r)), None)
    if chosen is None:
        room = {"below": screen.bottom() - region.bottom(), "above": region.top() - screen.top(),
                "right": screen.right() - region.right(), "left": region.left() - screen.left()}
        chosen = spots[max(room, key=room.get)]
    x = max(screen.left(), min(chosen.left(), screen.right() - w + 1))
    y = max(screen.top(), min(chosen.top(), screen.bottom() - h + 1))
    return QRect(x, y, w, h)


def resize_rect(orig: QRect, zone: str, delta: QPoint, bounds: QRect = None) -> QRect:
    """`orig` movido («move») o estirado por los lados/esquinas de `zone` («l», «tr»…) lo que se arrastró;
    nunca menor que MIN_REGION ni fuera de `bounds`."""
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


class _Line:
    def __init__(self, text, offset, rect, patch, bg, fg, font, words):
        self.text, self.offset, self.rect, self.patch = text, offset, rect, patch
        self.bg, self.fg, self.font, self.words = bg, fg, font, words


class RegionLayer(QWidget):
    """La región fija. Siempre recibe el mouse. Vacía solo se ve su marco; tras leer dibuja el texto sobre el original."""
    word_context = pyqtSignal(str, int)
    word_hovered = pyqtSignal(str, str, QPoint)
    word_anchor = pyqtSignal(QRect, bool)      # rect global de la palabra, y si el popup va arriba
    hover_ended = pyqtSignal()
    wheel_moved = pyqtSignal(int)
    word_clicked = pyqtSignal(str, str, int, str)       # palabra, compuesto, posición, texto completo
    key_pressed = pyqtSignal(int)                       # T / O / M / Esc con la región en foco
    select_requested = pyqtSignal()                     # clic derecho: elegir otra región
    region_edited = pyqtSignal(QRect)                   # se movió o estiró la región (al soltar el mouse)
    edit_finished = pyqtSignal()                        # doble clic dentro: terminar la edición

    def __init__(self, hover_delay_ms: int = 350):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)    # al mostrarse no le quita el foco al juego; con un clic sí lo toma
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMouseTracking(True)
        self.text_visible = True            # «O»: se dibuja el texto sobre el original
        self.stale = False                  # la pantalla cambió: el texto dibujado ya no corresponde y se quita
        self.editing = False                # se está moviendo / estirando la región
        self._frozen = None                 # QPixmap de la región congelada (se dibuja opaca bajo el texto)
        self.bounds = None                  # límites (la pantalla) para mover la región
        self._drag = None                   # (zona, posición global inicial, región inicial)
        self._lines = []
        self._fallback = ""                  # traducción, si no hay posiciones del texto (o es vertical)
        self._bg = QColor(20, 20, 24)
        self._fg = QColor(245, 245, 245)
        self._marks, self._pending = set(), set()
        self._hover_key = self._hover_pending = None
        self._hover_shown = False
        self._press = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(hover_delay_ms)
        self._timer.timeout.connect(self._emit_hover)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self._lines)

    @property
    def has_text(self) -> bool:
        return bool(self._lines) or bool(self._fallback)

    def clear(self):
        self._end_hover()
        self._lines, self._fallback = [], ""
        self.update()

    def set_editing(self, on: bool):
        self.editing = bool(on)
        self._end_hover()
        self._drag = None
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def _zone(self, pos) -> str:
        """Zona agarrada al editar: «move» dentro, o los lados que se estiran («l», «tr»…)."""
        w, h = self.width(), self.height()
        near_l, near_r = pos.x() <= GRAB, pos.x() >= w - 1 - GRAB
        near_t, near_b = pos.y() <= GRAB, pos.y() >= h - 1 - GRAB
        zone = ("t" if near_t else "b" if near_b else "") + ("l" if near_l else "r" if near_r else "")
        return zone or "move"

    CURSORS = {"move": Qt.SizeAllCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "t": Qt.SizeVerCursor,
               "b": Qt.SizeVerCursor, "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
               "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}

    @property
    def frozen(self) -> bool:
        return self._frozen is not None

    def set_frozen(self, image):
        """Congela la región con `image` (BGR, el recorte leído) o la descongela con None."""
        self._frozen = None if image is None else bgr_to_qpixmap(image)
        if self._frozen is not None:
            self.stale = False
        self.update()

    def set_stale(self, stale: bool):
        if self._frozen is not None:
            stale = False                      # congelada: lo de atrás no importa
        if bool(stale) != self.stale:
            self.stale = bool(stale)
            self._end_hover()
            self.update()

    def set_text_visible(self, visible: bool):
        self.text_visible = bool(visible)
        self._end_hover()
        self.update()

    def keyPressEvent(self, e):
        self.key_pressed.emit(e.key())

    def set_result(self, res, image, region: QRect):
        """Dibuja `res` (ocr_engine.OcrResult de `image`, el recorte de la región) sobre el original."""
        self.clear()
        h, w = image.shape[:2]
        sx, sy = w / max(1, region.width()), h / max(1, region.height())
        boxes = getattr(res, "boxes", None) or []
        if not boxes or getattr(res, "vertical", False) or len(boxes) < len(res.lines):
            self._fallback_colors(image)
            return                                             # la traducción llega después y se dibuja entonces
        entries = []
        for (text, _score), box in zip(res.lines, boxes):
            rect = QRectF(box[0] / sx, box[1] / sy, (box[2] - box[0]) / sx, (box[3] - box[1]) / sy)
            bg, fg = sample_colors(image, box)
            entries.append((text, rect, bg, fg))
        font = uniform_font([(t, r) for t, r, _b, _f in entries], self.font())
        fm = QFontMetrics(font)
        offset = 0
        for text, rect, bg, fg in entries:
            width = fm.horizontalAdvance(text)
            wide = rect.adjusted(0, 0, max(0.0, width - rect.width()), 0)
            words = [(s, e, wd, c, rect.left() + fm.horizontalAdvance(text[:s]), rect.left() + fm.horizontalAdvance(text[:e]))
                     for s, e, wd, c in iter_words(text)]
            self._lines.append(_Line(text, offset, rect, wide.adjusted(-3, -2, 3, 2), bg, fg, font, words))
            offset += len(text) + 1
        self.update()

    def _fallback_colors(self, image):
        h, w = image.shape[:2]
        self._bg, self._fg = sample_colors(image, (0, 0, w, h))

    def set_translation_fallback(self, text: str):
        """Sin posiciones del texto, la traducción se dibuja ocupando la región."""
        if not self._lines:
            self._fallback = text
            self.update()

    def set_marks(self, ranges, pending=()):
        self._marks, self._pending = set(ranges), set(pending)
        self.update()

    # ------------------------------------------------------------------ palabras
    def _hit(self, pos):
        if not self.text_visible or self.editing or self.stale:
            return None
        for li, line in enumerate(self._lines):
            if line.patch.contains(QPointF(pos)):
                for wi, w in enumerate(line.words):
                    if w[4] - 1 <= pos.x() <= w[5] + 1:
                        return li, wi
        return None

    def mouseMoveEvent(self, e):
        if self.editing:
            if self._drag is None:
                self.setCursor(self.CURSORS.get(self._zone(e.pos()), Qt.ArrowCursor))
            else:
                zone, start, orig = self._drag
                self.setGeometry(resize_rect(orig, zone, e.globalPos() - start, self.bounds))
            return
        hit = self._hit(e.pos())
        if hit == self._hover_key:
            return
        self._end_hover()
        self._hover_key = hit
        if hit:
            line = self._lines[hit[0]]
            w = line.words[hit[1]]
            r = QRectF(w[4], line.patch.top(), w[5] - w[4], line.patch.height()).toAlignedRect()
            self._hover_pending = (line.text, w[0], w[2], w[3], e.globalPos(),
                                   QRect(self.mapToGlobal(r.topLeft()), r.size()))
            self._timer.start()
        self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
        self.update()

    def _emit_hover(self):
        if self._hover_pending is None:
            return
        text, start, word, compound, pos, grect = self._hover_pending
        self._hover_shown = True
        screen = QApplication.screenAt(grect.center()) or QApplication.primaryScreen()
        above = self.geometry().center().y() > screen.geometry().center().y()   # región en la mitad inferior
        self.word_anchor.emit(grect, above)
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

    def hideEvent(self, e):
        self._end_hover()
        super().hideEvent(e)

    def wheelEvent(self, e):
        if self._hover_shown:
            self.wheel_moved.emit(e.angleDelta().y())

    def mousePressEvent(self, e):
        self._end_hover()
        if self.editing:
            if e.button() == Qt.LeftButton:
                self.activateWindow()
                self.setFocus()
                self._drag = (self._zone(e.pos()), e.globalPos(), QRect(self.geometry()))
            return
        if e.button() == Qt.RightButton:
            self.select_requested.emit()
            return
        self.activateWindow()                   # con el clic la región toma el foco y las teclas T/O/M/Esc funcionan
        self.setFocus()
        self._press = e.pos() if e.button() == Qt.LeftButton else None

    def mouseDoubleClickEvent(self, e):
        if self.editing:
            self.edit_finished.emit()

    def mouseReleaseEvent(self, e):
        if self.editing:
            if self._drag is not None:
                self._drag = None
                self.region_edited.emit(self.geometry())
            return
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
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))             # casi invisible, pero recibe el mouse
        if self._frozen is not None:
            p.drawPixmap(self.rect(), self._frozen)
        if self._fallback and self.text_visible and not self.editing and not self.stale:
            p.fillRect(self.rect(), QColor(self._bg.red(), self._bg.green(), self._bg.blue(), 245))
            font = QFont(self.font())
            font.setPixelSize(max(12, min(32, int(self.height() * 0.4))))
            p.setFont(font)
            p.setPen(self._fg)
            p.drawText(QRectF(self.rect()).adjusted(10, 6, -10, -6), Qt.AlignVCenter | Qt.TextWordWrap, self._fallback)
        for li, line in enumerate(self._lines if self.text_visible and not self.editing and not self.stale else []):
            p.fillRect(line.patch, line.bg)
            fm = QFontMetrics(line.font)
            for wi, w in enumerate(line.words):
                span = QRectF(w[4] - 1, line.patch.top() + 1, w[5] - w[4] + 2, line.patch.height() - 2)
                key = (line.offset + w[0], line.offset + w[1])
                if key in self._marks:
                    p.fillRect(span, MARK)
                elif key in self._pending:
                    p.fillRect(span, PENDING)
                if self._hover_key == (li, wi):
                    p.fillRect(span, QColor(255, 255, 255, 70))
                    p.setPen(QPen(line.fg, 1))
                    y = span.bottom() - 1
                    p.drawLine(QPointF(span.left(), y), QPointF(span.right(), y))
            p.setFont(line.font)
            p.setPen(line.fg)
            baseline = line.rect.center().y() + (fm.ascent() - fm.descent()) / 2
            p.drawText(QPointF(line.rect.left(), baseline), line.text)
        if self.editing:
            p.fillRect(self.rect(), QColor(0, 200, 255, 45))
            p.setPen(QPen(QColor(255, 255, 255, 235), 2))
            p.setBrush(Qt.NoBrush)
            r = self.rect().adjusted(1, 1, -2, -2)
            p.drawRect(r)
            p.setBrush(QColor(0, 200, 255, 255))
            for pt in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(), QPoint(r.center().x(), r.top()),
                       QPoint(r.center().x(), r.bottom()), QPoint(r.left(), r.center().y()), QPoint(r.right(), r.center().y())):
                p.drawRect(QRect(pt.x() - 4, pt.y() - 4, 8, 8))
        else:
            p.setPen(QPen(QColor(0, 200, 255, 220), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        p.end()


PANEL_STYLE = """
LivePanel { background: rgb(18, 20, 26); border: 1px solid rgba(255,255,255,70); border-radius: 8px; }
QLabel#head { color: #9aa3b2; font-size: 12px; }
QTextEdit { background: transparent; border: none; color: #b9e6ff; font-size: 16px; }
QPushButton { color: #f2f2f2; background: rgba(255,255,255,18); border: 1px solid rgba(255,255,255,50);
    border-radius: 5px; padding: 3px 9px; font-size: 13px; }
QPushButton:hover { background: rgba(255,255,255,45); }
"""


class LivePanel(QFrame):
    """Panel con la última traducción. No roba el foco, se arrastra por la franja de arriba y se estira por la esquina."""
    read_requested = pyqtSignal()
    toggle_requested = pyqtSignal()
    edit_requested = pyqtSignal()
    auto_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    freeze_requested = pyqtSignal()
    size_changed = pyqtSignal(QSize)             # el usuario le cambió el tamaño
    key_pressed = pyqtSignal(int)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("LivePanel")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(PANEL_STYLE)
        self.setMinimumSize(PANEL_MIN)
        self._region = QRect()
        self._user_size = None                   # tamaño que dejó el usuario
        self._moved = False
        self._drag = None
        self._placing = True
        self.head = QLabel("En vivo")
        self.head.setObjectName("head")
        self.head.setCursor(Qt.SizeAllCursor)
        self.btn_read = QPushButton("Leer")
        self.btn_toggle = QPushButton("Quitar texto (O)")
        self.btn_edit = QPushButton("Mover región (E)")
        self.btn_auto = QPushButton("Automático (A)")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setToolTip("Lee solo cuando el texto de la región cambia y se queda quieto (Ctrl+Alt+A)")
        self.btn_freeze = QPushButton("Congelar (F)")
        self.btn_stop = QPushButton("Detener")
        self._note = "En vivo"
        self._merge = True
        for b in (self.btn_read, self.btn_auto, self.btn_toggle, self.btn_edit, self.btn_freeze, self.btn_stop):
            b.setFocusPolicy(Qt.NoFocus)
        self.btn_read.clicked.connect(self.read_requested)
        self.btn_toggle.clicked.connect(self.toggle_requested)
        self.btn_edit.clicked.connect(self.edit_requested)
        self.btn_auto.clicked.connect(self.auto_requested)
        self.btn_stop.clicked.connect(self.stop_requested)
        self.btn_freeze.clicked.connect(self.freeze_requested)
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setFocusPolicy(Qt.NoFocus)
        self.view.setPlaceholderText("La traducción aparece aquí al pulsar «Leer».")
        self.grip = QSizeGrip(self)
        row = QHBoxLayout()
        row.setSpacing(5)
        row.addWidget(self.btn_read)
        row.addWidget(self.btn_auto)
        row.addWidget(self.btn_toggle)
        row.addWidget(self.btn_edit)
        row.addWidget(self.btn_freeze)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        row.addWidget(self.grip, 0, Qt.AlignBottom)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(4)
        lay.addWidget(self.head)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        self._placing = False

    def set_user_size(self, size):
        self._user_size = size if size is not None and size.width() >= PANEL_MIN.width() \
            and size.height() >= PANEL_MIN.height() else None

    def default_size(self, region: QRect) -> QSize:
        return QSize(max(380, min(700, region.width())), 190)

    def set_region(self, region: QRect):
        self._region = QRect(region)
        self._moved = False
        self.place()

    def place(self):
        if not self._region.isValid() or self._moved:
            return
        screen = QApplication.screenAt(self._region.center()) or QApplication.primaryScreen()
        geo = screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)
        size = self._user_size or self.default_size(self._region)
        self._placing = True
        try:
            self.setGeometry(compute_panel_rect(self._region, size, geo))
        finally:
            self._placing = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.isVisible() and not self._placing:
            self._user_size = self.size()
            self.size_changed.emit(self.size())

    def keyPressEvent(self, e):
        self.key_pressed.emit(e.key())

    def set_text(self, text: str):
        self.view.setPlainText(text)

    def set_note(self, note: str):
        self._note = note
        self._refresh_head()

    def set_merge(self, merge: bool):
        """Indica si las líneas partidas se están uniendo (tecla M)."""
        self._merge = bool(merge)
        self._refresh_head()

    def _refresh_head(self):
        self.head.setText(f"{self._note}  ·  Líneas {'unidas' if self._merge else 'separadas'} (M)")

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


class LiveMode(QObject):
    active_changed = pyqtSignal(bool)
    message = pyqtSignal(str)
    select_requested = pyqtSignal()          # clic derecho en la región: elegir otra
    auto_changed = pyqtSignal(bool)

    def __init__(self, win, grab_fn=None, exclude_fn=None):
        super().__init__(win)
        self.win = win
        self._grab = grab_fn or grab_region
        self._exclude = exclude_fn or exclude_from_capture
        self.controller = LiveController(lambda box: self._grab(box), parent=self)
        self.controller.configure()
        self.controller.hold_fn = self._popup_hold
        self._popup_was_visible = False
        self.controller.capture.connect(self.on_auto_capture)
        self.controller.state_changed.connect(self._on_ctrl_state)
        saved = win.settings.value("live_auto", False)
        self.auto = saved in (True, "true", "1", 1)          # lectura automática (se recuerda)
        self.capture_safe = False                            # ¿la región se excluye de las capturas? (hace falta para vigilar)
        self.kind = "manual"                                 # quién pidió la lectura en curso: «manual» o «auto»
        self.skip = False                                    # esa lectura se descarta (vacía o repetida)
        self._paused_before = False
        self.layer = RegionLayer()
        self.panel = LivePanel()
        self.box = None                      # (x0, y0, x1, y1) en píxeles reales
        self.rect = None                     # QRect global en píxeles lógicos
        self.active = False
        self.inflight = False                # hay una lectura en curso pedida por el modo en vivo
        self.panel_visible = True            # «T»: el panel con la traducción
        self.text_visible = True             # «O»: el texto dibujado sobre el original
        self._reading = False                # entre pulsar «Leer» y recibir la captura
        self._image = None
        self.editing = False                 # se está moviendo / estirando la región
        self.freeze_read = win.settings.value("live_freeze_read", False) in (True, "true", "1", 1)
        self.freeze_auto = win.settings.value("live_freeze_auto", False) in (True, "true", "1", 1)
        self._scale = (1.0, 1.0)             # píxeles reales por píxel lógico
        self._origin = (0.0, 0.0)            # esquina del monitor en coordenadas lógicas
        self._hidden_for_select = False
        w = win.words
        self.layer.word_context.connect(w.set_context)
        self.layer.word_anchor.connect(self._set_anchor)
        self.layer.word_hovered.connect(w.hover)
        self.layer.hover_ended.connect(w.hover_left)
        self.layer.hover_ended.connect(self._clear_anchor)
        self.layer.wheel_moved.connect(w.scroll_popup)
        self.layer.word_clicked.connect(self._word_clicked)
        self.panel.read_requested.connect(self.read_now)
        self.panel.toggle_requested.connect(self.toggle_text)
        self.panel.edit_requested.connect(self.toggle_edit)
        self.panel.auto_requested.connect(self.toggle_auto)
        self.panel.freeze_requested.connect(self.toggle_freeze)
        self.panel.btn_auto.setChecked(self.auto)
        self.layer.region_edited.connect(self._apply_rect)
        self.layer.edit_finished.connect(lambda: self.set_editing(False))
        win.chk_merge.toggled.connect(self.panel.set_merge)
        self.panel.set_merge(win.chk_merge.isChecked())
        self.panel.stop_requested.connect(self.stop)
        self.panel.size_changed.connect(self._save_size)
        self.layer.key_pressed.connect(self.handle_key)
        self.panel.key_pressed.connect(self.handle_key)
        self.layer.select_requested.connect(self.select_requested)
        size = win.settings.value("live_panel_size")
        if isinstance(size, QSize):
            self.panel.set_user_size(size)

    # ------------------------------------------------------------------ control
    def start(self, box, rect: QRect):
        self.box = tuple(int(v) for v in box)
        self.rect = QRect(rect)
        self.active = True
        self.inflight = self._reading = False
        self.text_visible = True
        self.layer.set_editing(False)
        self.editing = False
        self.panel.btn_edit.setText("Mover región (E)")
        self._compute_scale()
        screen = QApplication.screenAt(self.rect.center()) or QApplication.primaryScreen()
        self.layer.bounds = screen.geometry() if screen is not None else None
        self.layer.clear()
        self.layer.setGeometry(self.rect)
        self.panel.set_text("")
        self.panel.set_note("En vivo · pulsa «Leer» o Ctrl+Alt+R")
        self.panel_visible = True
        self.layer.set_text_visible(True)
        self._sync_toggle_text()
        self.panel.set_region(self.rect)
        self.layer.show()
        self.panel.setVisible(self.panel_visible)
        self.layer.set_stale(False)
        self.capture_safe = bool(self._exclude(self.layer))
        self.skip = False
        self.kind = "manual"
        self.active_changed.emit(True)
        if self.auto:
            self.set_auto(True)

    def stop(self):
        was = self.active
        self.layer.set_editing(False)
        self.editing = False
        self._ghost(False)
        self._set_frozen(None)
        self.controller.stop()
        self.layer.set_stale(False)
        self.active = False
        self.inflight = self._reading = False
        self.layer.clear()
        self.layer.hide()
        self.panel.hide()
        if was:
            self.active_changed.emit(False)

    def has_region(self) -> bool:
        return self.box is not None

    def set_freeze_options(self, read=None, auto=None):
        if read is not None:
            self.freeze_read = bool(read)
        if auto is not None:
            self.freeze_auto = bool(auto)

    def _set_frozen(self, image):
        self.layer.set_frozen(image)
        self.panel.btn_freeze.setText("Descongelar (F)" if image is not None else "Congelar (F)")

    def toggle_freeze(self):
        """«F» / Ctrl+Alt+F: congela la región con la última lectura, o la descongela."""
        if not self.active:
            return
        if self.layer.frozen:
            self._set_frozen(None)
        elif self._image is not None and self.layer.has_text:
            self._set_frozen(self._image)

    def toggle_text(self):
        """«O» / Ctrl+Alt+H: muestra u oculta el texto dibujado sobre el original. La región (y el mouse) siguen."""
        if not self.active:
            return
        self.text_visible = not self.text_visible
        self.layer.set_text_visible(self.text_visible)
        self.win.words.hide_popup()
        self._sync_toggle_text()

    def toggle_panel(self):
        """«T» / Ctrl+Alt+T: muestra u oculta el panel con la traducción."""
        if not self.active:
            return
        self.panel_visible = not self.panel_visible
        self.panel.setVisible(self.panel_visible)
        self._sync_toggle_text()

    def _sync_toggle_text(self):
        self.panel.btn_toggle.setText("Quitar texto (O)" if self.text_visible else "Poner texto (O)")

    def toggle_merge(self):
        """«M» / Ctrl+Alt+M: une o separa las líneas partidas y vuelve a procesar la última lectura."""
        if not self.active or self._reading or self.inflight or self._image is None:
            return
        win = self.win
        win.chk_merge.setChecked(not win.chk_merge.isChecked())
        self.message.emit("Líneas unidas" if win.chk_merge.isChecked() else "Líneas separadas")
        self.kind = "manual"
        self.inflight = True
        self.controller.set_busy(True)
        win._request(self._image)

    # --- mover / estirar la región ---
    def _compute_scale(self):
        if self.box is None or self.rect is None:
            return
        self._scale = ((self.box[2] - self.box[0]) / max(1, self.rect.width()),
                       (self.box[3] - self.box[1]) / max(1, self.rect.height()))
        self._origin = (self.rect.x() - self.box[0] / self._scale[0], self.rect.y() - self.box[1] / self._scale[1])

    def toggle_edit(self):
        if self.active:
            self.set_editing(not self.editing)

    def set_editing(self, on: bool):
        if not self.active or on == self.editing or self._reading or self.inflight:
            return
        self.editing = on
        self.win.words.hide_popup()
        self.layer.set_editing(on)
        self._pause_auto(on)
        self.panel.btn_edit.setText("Terminar edición (E)" if on else "Mover región (E)")
        self.panel.set_note("En vivo · editando la región: arrastra dentro para moverla o los bordes para estirarla"
                            if on else "En vivo · región actualizada")
        if on:
            self.layer.activateWindow()
            self.layer.setFocus()

    def _apply_rect(self, rect: QRect):
        """La región se movió o cambió de tamaño: se recalcula la caja de captura (px reales) y se recoloca el panel."""
        sx, sy = self._scale
        ox, oy = self._origin
        x0, y0 = int(round((rect.x() - ox) * sx)), int(round((rect.y() - oy) * sy))
        x1, y1 = int(round((rect.right() + 1 - ox) * sx)), int(round((rect.bottom() + 1 - oy) * sy))
        self.box = (max(0, x0), max(0, y0), max(x0 + 1, x1), max(y0 + 1, y1))
        self.rect = QRect(rect)
        self._set_frozen(None)
        self.layer.clear()                        # el texto de antes ya no encaja con la región nueva
        self.panel.set_region(self.rect)
        if self.controller.box is not None:
            self.controller.set_box(self.box)

    def handle_key(self, key):
        """Las mismas teclas que la captura congelada, con la región o el panel en foco."""
        if key == Qt.Key_Escape:
            self.stop()
        elif key == Qt.Key_T:
            self.toggle_panel()
        elif key == Qt.Key_O:
            self.toggle_text()
        elif key == Qt.Key_M:
            self.toggle_merge()
        elif key == Qt.Key_E:
            self.toggle_edit()
        elif key == Qt.Key_A:
            self.toggle_auto()
        elif key == Qt.Key_F:
            self.toggle_freeze()

    # --- elegir otra región: se esconde todo para que no salga en la captura congelada ---
    def hide_for_select(self):
        if self.active and not self._hidden_for_select:
            self._hidden_for_select = True
            self._pause_auto(True)
            self.layer.hide()
            self.panel.hide()

    def restore_after_select(self):
        if not self._hidden_for_select:
            return
        self._hidden_for_select = False
        if self.active:
            self.layer.show()
            self.panel.setVisible(self.panel_visible)
            self._pause_auto(False)

    # ------------------------------------------------------------------ lectura
    def read_now(self):
        if not self.active or self._reading or self.inflight:
            return
        if self.editing:
            self.set_editing(False)
        self._reading = True
        self.controller.set_busy(True)
        self.win.words.hide_popup()
        self._ghost(True)                       # un instante, para no leer el propio texto
        self.panel.set_note("En vivo · leyendo…")
        QTimer.singleShot(READ_DELAY_MS, self._capture)

    def _capture(self):
        if not self._reading or not self.active:
            return
        try:
            image = self._grab(self.box)
        except Exception as exc:  # noqa: BLE001
            self._reading = False
            self._ghost(False)
            self.controller.set_busy(False)
            self.panel.set_note(f"En vivo · no se pudo capturar la región ({exc})")
            return
        self._reading = False
        self._image = image
        self.kind = "manual"
        self.inflight = True
        self.skip = False
        if self.controller.active:                 # lo que hay en pantalla cuenta como ya leído
            self.controller.detector.mark_read(signature(image), self.controller._now())
        self.layer.clear()                      # lo viejo sale; lo nuevo llega con el resultado
        self._ghost(False)
        self.win.request_live(image)

    def _ghost(self, on: bool):
        """Vuelve invisibles (opacidad 0) la región y el panel mientras se captura. No se ocultan de verdad: así
        conservan el foco del teclado y las teclas T/O/M/E/Esc siguen funcionando tras leer."""
        self.layer.setWindowOpacity(0.0 if on else 1.0)
        self.panel.setWindowOpacity(0.0 if on else 1.0)

    # --- lectura automática ---
    def toggle_auto(self):
        self.set_auto(not self.auto)

    def set_auto(self, on: bool):
        on = bool(on)
        if on and self.active and not self.capture_safe:
            self.message.emit("La lectura automática necesita que Windows deje excluir la región de las capturas "
                              "(Windows 10 versión 2004 o superior). Usa la lectura manual.")
            on = False
        self.auto = on
        self.win.settings.setValue("live_auto", on)
        self.panel.btn_auto.setChecked(on)
        if self.active:
            if on:
                self.controller.start(self.box)
                self._pause_auto(self.editing or self._hidden_for_select)
            else:
                self.controller.stop()
                self.layer.set_stale(False)
            self.panel.set_note(self._base_note())
        self.auto_changed.emit(on)

    def _pause_auto(self, pause: bool):
        """Se deja de vigilar mientras se edita la región o se elige otra; al volver se empieza de cero."""
        if not self.controller.active:
            return
        if pause:
            self._paused_before = self.controller.paused
            self.controller.set_paused(True)
        else:
            self.controller.detector.reset()
            self.controller.set_paused(self._paused_before)

    def _set_anchor(self, rect, above):
        popup = getattr(self.win, "popup", None)
        if popup is not None:
            popup.set_anchor(rect, above)

    def _clear_anchor(self):
        popup = getattr(self.win, "popup", None)
        if popup is not None:
            popup.set_anchor(None)

    def _popup_hold(self) -> bool:
        """Mientras la ventanita del diccionario está a la vista no se lee: aparece encima de la región y cambiaría lo
        que se ve. Además se la excluye de las capturas (cada vez que sale) para que no la lea ni el OCR manual."""
        popup = getattr(self.win, "popup", None)
        visible = bool(popup is not None and popup.isVisible())
        if visible and not self._popup_was_visible:
            try:
                self._exclude(popup)
            except Exception:  # noqa: BLE001
                pass
        self._popup_was_visible = visible
        return visible

    def _base_note(self) -> str:
        return "En vivo · automático" if self.auto else "En vivo"

    def _on_ctrl_state(self, state):
        if state == "changing":
            self.layer.set_stale(True)           # la pantalla cambió: el texto dibujado ya no corresponde
            self.win.words.hide_popup()
        if self.auto and not self.inflight and not self._reading and not self.editing:
            names = {"watching": "vigilando", "changing": "el texto cambia…", "reading": "leyendo…", "paused": "en pausa"}
            if state in names:
                self.panel.set_note(f"En vivo · automático · {names[state]}")

    def on_auto_capture(self, image):
        """El detector vio un cambio que ya se quedó quieto: se lee."""
        if not self.active or self.inflight or self._reading or self.editing or self._hidden_for_select:
            return
        self._image = image
        self.kind = "auto"
        self.skip = False
        self.inflight = True
        self.controller.set_busy(True)
        self.win.request_live(image)

    # --- resultados (los reenvía la ventana principal) ---
    def accept_result(self, res) -> bool:
        """Al llegar el OCR de una lectura en vivo: False si se descarta. Solo las automáticas se descartan: vacías
        (la región ya no tiene texto) o con el mismo texto que antes (cambió algo que no era el diálogo)."""
        if self.kind != "auto":
            return True
        if not res.text.strip():
            self.skip = True
            self.layer.clear()
            self.panel.set_note("En vivo · automático · sin texto en la región")
            return False
        if not self.controller.accept_text(res.text):
            self.skip = True
            self.layer.set_stale(False)           # mismo texto de antes: el que había se vuelve a mostrar
            self.panel.set_note(self._base_note())
            return False
        return True

    def show_result(self, res):
        if not self.inflight:
            return
        if not res.text.strip():
            self._set_frozen(None)
            self.layer.clear()
            self.panel.set_text("")
            self.panel.set_note("En vivo · sin texto en la región")
            return
        self.controller.accept_text(res.text)
        self.layer.set_result(res, self._image, self.rect)
        want = self.freeze_auto if self.kind == "auto" else self.freeze_read
        self._set_frozen(self._image if want else None)
        self.layer.set_stale(False)
        self.panel.set_note("En vivo · traduciendo…")
        self.refresh_marks()

    def show_translation(self, text: str):
        if not self.inflight or self.skip:
            return
        self.panel.set_text(text)
        self.panel.set_note(self._base_note())
        self.layer.set_translation_fallback(text)

    def finished(self):
        self.inflight = False
        self.skip = False
        self.controller.set_busy(False)

    def failed(self, msg: str):
        if self.inflight:
            self.panel.set_note(f"En vivo · {msg}")

    def refresh_marks(self):
        text = self.layer.text
        if text:
            self.layer.set_marks(self.win.words.marks(text), self.win.words.pending_marks(text))

    def _word_clicked(self, word, compound, start, text):
        self.win.words.click(word, compound, text, start, self.win._merge_used)

    def _save_size(self, size):
        self.win.settings.setValue("live_panel_size", size)

    def shutdown(self):
        self.stop()
        self.layer.close()
        self.panel.close()
