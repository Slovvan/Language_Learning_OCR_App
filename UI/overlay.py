"""Capa a pantalla completa sobre la captura congelada.

Flujo:  elegir región  ->  «leyendo»  ->  el texto reconocido se dibuja encima del original.

Mientras está abierta, la pantalla real queda tapada por la captura, así que el juego puede
seguir (o ser una cinemática) sin que cambie lo que estás leyendo. Sobre las palabras del texto
de la capa funciona lo mismo que en la ventana principal: ventanita del diccionario al dejar
quieto el mouse, clic para guardar y marca verde en las ya guardadas.

Teclas:  Esc cierra · T muestra/oculta la traducción · O muestra/oculta el texto de la capa ·
         M une o separa las líneas partidas · clic derecho: elegir otra región.
"""
import html
from dataclasses import dataclass, field

import numpy as np
from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPen, QRegion,
                         QTextDocument)
from PyQt5.QtWidgets import QApplication, QWidget

import Translator.languages as languages
from UI.capture import bgr_to_qpixmap
from UI.word_popup import iter_words

SELECT, READING, TEXT = range(3)          # estados de la capa

DIM = QColor(0, 0, 0, 120)                # oscurece lo que queda fuera de la región
ACCENT = QColor(0, 200, 255)
MARK = QColor(46, 190, 80, 150)           # fondo de las palabras ya guardadas
PENDING = QColor(240, 150, 30, 150)       # fondo de las palabras sin significado (pendientes)
HOVER = QColor(255, 255, 255, 70)
PAD_X, PAD_Y = 3, 2                       # cuánto sobresale el parche del cuadro del OCR
RING = 3                                  # grosor (px) del borde que se mira para sacar el color de fondo
HINT_SELECT = "Arrastra para elegir la región · Esc cancela"
HINT_READING = "Leyendo el texto…"
HINT_TEXT = "Clic: guardar palabra · T traducción · O original · M unir líneas · Clic der.: otra región · Esc"
HINT_EMPTY = "No se detectó texto · Clic derecho: otra región · Esc cierra"


@dataclass
class _Word:
    start: int          # posición dentro de la línea
    end: int
    word: str
    compound: str
    x0: float           # extremos horizontales (px lógicos de la pantalla); en una columna vertical, los verticales
    x1: float


@dataclass
class _Line:
    text: str
    offset: int         # posición de la línea dentro del texto completo
    rect: QRectF        # cuadro del texto original (px lógicos)
    patch: QRectF       # lo que se tapa: rect con un margen
    bg: QColor
    fg: QColor
    font: QFont
    words: list = field(default_factory=list)
    vertical: bool = False   # columna de texto vertical: los caracteres se apilan de arriba abajo
    cell: float = 0.0        # alto de cada carácter en una columna vertical


def sample_colors(crop: np.ndarray, box) -> tuple:
    """(fondo, texto) de un cuadro de la captura, como QColor.

    El fondo es la mediana de un borde fino alrededor del cuadro (ahí casi no hay letras);
    el texto es el promedio de los píxeles más distintos al fondo dentro del cuadro.
    Si ambos quedan parecidos se usa blanco o negro según lo claro que sea el fondo.
    """
    h, w = crop.shape[:2]
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = min(w, int(np.ceil(box[2]))), min(h, int(np.ceil(box[3])))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return QColor(30, 30, 30), QColor(255, 255, 255)
    inside = crop[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    ex0, ey0, ex1, ey1 = max(0, x0 - RING), max(0, y0 - RING), min(w, x1 + RING), min(h, y1 + RING)
    mask = np.ones((ey1 - ey0, ex1 - ex0), dtype=bool)
    mask[y0 - ey0:y1 - ey0, x0 - ex0:x1 - ex0] = False
    ring = crop[ey0:ey1, ex0:ex1][mask].astype(np.float32)
    bg = np.median(ring if len(ring) >= 12 else inside, axis=0)
    dist = np.linalg.norm(inside - bg, axis=1)
    far = inside[np.argsort(dist)[-max(1, int(len(dist) * 0.08)):]]
    fg = far.mean(axis=0)

    def lum(c):                                            # los píxeles están en BGR
        return 0.114 * c[0] + 0.587 * c[1] + 0.299 * c[2]

    if abs(lum(fg) - lum(bg)) < 80:
        fg = np.array([255, 255, 255] if lum(bg) < 128 else [0, 0, 0], dtype=np.float32)
    return (QColor(int(bg[2]), int(bg[1]), int(bg[0])), QColor(int(fg[2]), int(fg[1]), int(fg[0])))


def fit_font(text: str, rect: QRectF, base: QFont) -> QFont:
    """Fuente cuyo texto cabe en el cuadro: tan alta como el cuadro y sin pasarse de ancho."""
    font = QFont(base)
    font.setStyleStrategy(QFont.PreferAntialias)
    size = max(8, int(rect.height() * 0.78))
    while True:
        font.setPixelSize(size)
        if size <= 8 or QFontMetrics(font).horizontalAdvance(text) <= rect.width() + 4:
            return font
        size -= 1


def uniform_font(items: list, base: QFont) -> QFont:
    """Una sola fuente para todas las líneas [(texto, rect)]: la más pequeña que cabe en alguna,
    pero nunca por debajo de 3/4 de la mediana de los tamaños individuales."""
    sizes = sorted(fit_font(t, r, base).pixelSize() for t, r in items)
    if not sizes:
        return fit_font("", QRectF(0, 0, 100, 12), base)
    median = sizes[len(sizes) // 2]
    size = max(sizes[0], int(median * 0.75), 8)
    font = QFont(base)
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setPixelSize(size)
    return font


class CaptureOverlay(QWidget):
    region_selected = pyqtSignal(object)           # recorte (BGR) para el OCR
    word_hovered = pyqtSignal(str, str, QPoint)    # palabra, compuesto, posición global
    word_context = pyqtSignal(str, int)            # justo antes: línea de texto y posición de la palabra en ella
    hover_ended = pyqtSignal()
    wheel_moved = pyqtSignal(int)                  # rueda del mouse: desplaza la ventanita del diccionario
    word_clicked = pyqtSignal(str, str, int)       # palabra, compuesto, posición en `self.text`
    merge_toggled = pyqtSignal()                   # tecla M
    closed = pyqtSignal()                          # se cerró (Esc o cancelada la selección)

    def __init__(self, hover_delay_ms: int = 350):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._frame = None
        self._pixmap = None
        self._state = SELECT
        self._origin = None                        # arrastre de la selección
        self._current = None
        self._region = None                        # QRectF de la región elegida (px lógicos)
        self._crop = None
        self._scale = (1.0, 1.0)                   # píxeles reales por píxel lógico
        self._lines = []
        self.text = ""                             # texto completo de la capa (líneas unidas con \n)
        self._marks = set()                        # (inicio, fin) de las palabras guardadas
        self._pending = set()                      # ...y de las pendientes (sin significado)
        self._translation = ""
        self._message = ""                         # error u otro aviso que sustituye a la traducción
        self._busy = False                         # esperando la traducción
        self._show_translation = True
        self._show_layer = True
        self._toast = ""
        self._panel = None                         # (QRectF, QTextDocument) del panel de traducción
        self._hover_key = None                     # (línea, palabra) bajo el mouse
        self._hover_pending = None
        self._hover_ctx = None                     # (línea, posición) de la palabra bajo el mouse
        self._hover_shown = False
        self._press = None

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(hover_delay_ms)
        self._hover_timer.timeout.connect(self._emit_hover)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(2500)
        self._toast_timer.timeout.connect(self._clear_toast)

    # ------------------------------------------------------------------ ciclo de vida
    @property
    def state(self) -> int:
        return self._state

    def open(self, frame: np.ndarray):
        """Muestra la captura congelada a pantalla completa y empieza la selección."""
        self._frame = frame
        self._pixmap = bgr_to_qpixmap(frame)
        self._reset_to_select()
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.activateWindow()
        self.raise_()
        self.setFocus()

    def dismiss(self):
        """Cierra la capa (el texto y la traducción se conservan en el registro, no aquí)."""
        self._end_hover()
        self._toast_timer.stop()
        self.hide()
        self.closed.emit()

    def _reset_to_select(self):
        self._end_hover()
        self._state = SELECT
        self._origin = self._current = None
        self._region = self._crop = None
        self._lines, self.text = [], ""
        self._marks = set()
        self._pending = set()
        self._translation = self._message = ""
        self._busy = False
        self._panel = None
        self.setCursor(Qt.CrossCursor)
        self.update()

    # ------------------------------------------------------------------ selección
    def _finish_select(self, rect: QRect):
        if rect.width() < 5 or rect.height() < 5:
            self.dismiss()
            return
        fh, fw = self._frame.shape[:2]
        sx, sy = fw / self.width(), fh / self.height()
        x0, y0 = max(0, int(rect.left() * sx)), max(0, int(rect.top() * sy))
        x1, y1 = min(fw, int((rect.right() + 1) * sx)), min(fh, int((rect.bottom() + 1) * sy))
        if x1 - x0 < 5 or y1 - y0 < 5:
            self.dismiss()
            return
        self._crop = self._frame[y0:y1, x0:x1].copy()
        self._scale = (sx, sy)
        self._region = QRectF(x0 / sx, y0 / sy, (x1 - x0) / sx, (y1 - y0) / sy)
        self._origin = self._current = None
        self._state = READING
        self._busy = False
        self.setCursor(Qt.ArrowCursor)
        self.update()
        self.region_selected.emit(self._crop)

    # ------------------------------------------------------------------ resultados
    def set_result(self, res):
        """Dibuja el texto reconocido encima del original. `res` es un ocr_engine.OcrResult."""
        self._end_hover()
        self._lines, texts = [], []
        self._vertical = bool(getattr(res, "vertical", False))
        offset = 0
        base = self.font()
        sx, sy = self._scale
        ox, oy = self._region.left(), self._region.top()
        n = len(res.lines)
        entries = []                                # (texto, rect, fondo, color de texto)
        for i, (text, _score) in enumerate(res.lines):
            if i < len(res.boxes):
                bx0, by0, bx1, by1 = res.boxes[i]
                rect = QRectF(ox + bx0 / sx, oy + by0 / sy, (bx1 - bx0) / sx, (by1 - by0) / sy)
                bg, fg = sample_colors(self._crop, res.boxes[i])
            else:                                   # sin cajas: se reparte la región en filas iguales
                row = self._region.height() / max(1, n)
                rect = QRectF(self._region.left(), self._region.top() + i * row,
                              self._region.width(), row)
                bg, fg = QColor(25, 25, 25), QColor(255, 255, 255)
            entries.append((text, rect, bg, fg))
        # Un solo tamaño para todo el texto: el que cabe en la línea más ajustada (sin bajar de
        # tres cuartos de la mediana, para que una línea rara no encoja todas las demás).
        font = uniform_font([(t, r) for t, r, _b, _f in entries if not self._is_vertical(t, r)], base)
        fm = QFontMetrics(font)
        for text, rect, bg, fg in entries:
            if self._is_vertical(text, rect):
                self._add_vertical(text, rect, bg, fg, base, offset)
                texts.append(text)
                offset += len(text) + 1
                continue
            width = fm.horizontalAdvance(text)
            wide = rect.adjusted(0, 0, max(0.0, width - rect.width()), 0)   # si se pasa, el parche la cubre
            words = [_Word(s, e, w, c, rect.left() + fm.horizontalAdvance(text[:s]),
                           rect.left() + fm.horizontalAdvance(text[:e]))
                     for s, e, w, c in iter_words(text)]
            patch = wide.adjusted(-PAD_X, -PAD_Y, PAD_X, PAD_Y)
            self._lines.append(_Line(text, offset, rect, patch, bg, fg, font, words))
            texts.append(text)
            offset += len(text) + 1
        self.text = "\n".join(texts)
        self._translation = self._message = ""
        self._busy = bool(self.text.strip())
        self._state = TEXT
        self._rebuild_panel()
        self.update()

    def _is_vertical(self, text: str, rect: QRectF) -> bool:
        return self._vertical and len(text.strip()) > 0

    def _add_vertical(self, text, rect, bg, fg, base, offset):
        """Columna de texto vertical: cada carácter en su celda, de arriba abajo, en el sitio original."""
        cell = max(8.0, min(rect.width(), rect.height() / max(1, len(text))))
        font = QFont(base)
        font.setStyleStrategy(QFont.PreferAntialias)
        font.setPixelSize(max(8, int(cell * 0.82)))
        height = cell * len(text)
        box = QRectF(rect.center().x() - cell / 2, rect.top(), cell, max(height, rect.height()))
        words = [_Word(s, e, w, c, box.top() + s * cell, box.top() + e * cell) for s, e, w, c in iter_words(text)]
        patch = box.adjusted(-PAD_X, -PAD_Y, PAD_X, PAD_Y)
        self._lines.append(_Line(text, offset, box, patch, bg, fg, font, words, True, cell))

    def set_translation(self, text: str):
        self._translation = text
        self._busy = False
        self._rebuild_panel()
        self.update()

    def set_message(self, text: str):
        """Un error u otro aviso que se muestra en el panel en lugar de la traducción."""
        self._message = text
        self._busy = False
        self._rebuild_panel()
        self.update()

    def set_marks(self, ranges, pending=()):
        """Palabras ya guardadas y pendientes: rangos (inicio, fin) del texto completo (`self.text`)."""
        self._marks = set(ranges)
        self._pending = set(pending)
        self.update()

    def show_toast(self, text: str):
        self._toast = text
        self._toast_timer.start()
        self.update()

    def _clear_toast(self):
        self._toast = ""
        self.update()

    # ------------------------------------------------------------------ panel de traducción
    def _rebuild_panel(self):
        body = self._message or self._translation or ("Traduciendo…" if self._busy else "")
        if not body or self._region is None or self.width() <= 0:
            self._panel = None
            return
        font = QFont(self.font())
        font.setPixelSize(17)
        doc = QTextDocument()
        doc.setDefaultFont(font)
        color = "#ffb4b4" if self._message else "#f4f4f4"
        doc.setHtml(f"<div style='color:{color}'>{html.escape(body).replace(chr(10), '<br>')}</div>")
        width = min(max(self._region.width(), 380.0), self.width() - 40.0)
        x = min(max(self._region.left(), 20.0), self.width() - 20.0 - width)
        gap, margin = 8.0, 12.0
        space_below = self.height() - margin - (self._region.bottom() + gap)
        space_above = (self._region.top() - gap) - margin
        # Región en la mitad de abajo -> el panel va arriba (y al revés), para no tapar el texto
        # ni quedar pegado al borde. Si no cabe en ese lado, se prueba el otro.
        center = (self._region.top() + self._region.bottom()) / 2
        order = ["above", "below"] if center > self.height() / 2 else ["below", "above"]
        while True:
            doc.setTextWidth(width - 28)
            height = doc.size().height() + 24
            fits = {"above": height <= space_above, "below": height <= space_below}
            side = next((sd for sd in order if fits[sd]), None)
            if side is not None or font.pixelSize() <= 12:
                break
            font.setPixelSize(font.pixelSize() - 1)            # ningún lado alcanza: letra más chica
            doc.setDefaultFont(font)
        if side is None:                                       # sigue sin caber: el lado con más espacio
            side = "above" if space_above >= space_below else "below"
        if side == "below":
            y = min(self._region.bottom() + gap, self.height() - margin - height)
        else:
            y = max(self._region.top() - gap - height, margin)
        self._panel = (QRectF(x, y, width, height), doc)

    def wheelEvent(self, e):
        self.wheel_moved.emit(e.angleDelta().y())
        e.accept()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rebuild_panel()

    # ------------------------------------------------------------------ palabra bajo el mouse
    def _hit(self, pos):
        """(línea, palabra) bajo `pos` (coordenadas del widget) o None."""
        if self._state != TEXT or not self._show_layer:
            return None
        point = QPointF(pos)
        for li, line in enumerate(self._lines):
            if line.patch.contains(point):
                coord = point.y() if line.vertical else point.x()
                for wi, w in enumerate(line.words):
                    if w.x0 <= coord < w.x1:
                        return li, wi
                return None
        return None

    def _emit_hover(self):
        if self._hover_pending:
            self._hover_shown = True
            if self._hover_ctx:
                self.word_context.emit(*self._hover_ctx)
            self.word_hovered.emit(*self._hover_pending)

    def _end_hover(self):
        self._hover_timer.stop()
        self._hover_key = None
        self._hover_pending = None
        self._hover_ctx = None
        if self._hover_shown:
            self._hover_shown = False
            self.hover_ended.emit()

    # ------------------------------------------------------------------ eventos
    def mousePressEvent(self, e):
        self._end_hover()
        if self._state == SELECT:
            if e.button() == Qt.RightButton:
                self.dismiss()
            elif e.button() == Qt.LeftButton:
                self._origin = self._current = e.pos()
                self.update()
            return
        if e.button() == Qt.RightButton:               # volver a elegir región sobre la misma captura
            self._reset_to_select()
        elif e.button() == Qt.LeftButton:
            self._press = e.pos()

    def mouseMoveEvent(self, e):
        if self._state == SELECT:
            if self._origin is not None:
                self._current = e.pos()
                self.update()
            return
        hit = self._hit(e.pos())
        if hit == self._hover_key:
            return
        self._end_hover()
        self._hover_key = hit
        if hit:
            line = self._lines[hit[0]]
            w = line.words[hit[1]]
            self._hover_pending = (w.word, w.compound, e.globalPos())
            self._hover_ctx = (line.text, w.start)
            self._hover_timer.start()
        self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._state == SELECT:
            if self._origin is not None:
                rect = QRect(self._origin, e.pos()).normalized()
                self._origin = self._current = None
                self._finish_select(rect)
            return
        press, self._press = self._press, None
        if press is None or (e.pos() - press).manhattanLength() > 4:
            return
        hit = self._hit(e.pos())
        if hit:
            line = self._lines[hit[0]]
            w = line.words[hit[1]]
            self.word_clicked.emit(w.word, w.compound, line.offset + w.start)

    def leaveEvent(self, e):
        self._end_hover()
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key_Escape:
            self.dismiss()
        elif self._state == TEXT and key == Qt.Key_T:
            self._show_translation = not self._show_translation
            self.update()
        elif self._state == TEXT and key == Qt.Key_O:
            self._show_layer = not self._show_layer
            self._end_hover()
            self.update()
        elif self._state in (READING, TEXT) and key == Qt.Key_M:
            self.merge_toggled.emit()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------ dibujo
    def _hole(self):
        if self._origin is not None and self._current is not None:
            return QRect(self._origin, self._current).normalized()
        if self._region is not None:
            return self._region.toAlignedRect()
        return None

    def paintEvent(self, _):
        if self._pixmap is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.drawPixmap(self.rect(), self._pixmap)

        hole = self._hole()
        p.save()
        if hole is not None:
            p.setClipRegion(QRegion(self.rect()).subtracted(QRegion(hole)))
        p.fillRect(self.rect(), DIM)
        p.restore()

        if self._state == TEXT and self._show_layer:
            self._paint_layer(p)
        if hole is not None:
            p.setPen(QPen(ACCENT, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(hole)
        if self._state in (READING, TEXT) and self._show_translation and self._panel is not None:
            rect, doc = self._panel
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.setBrush(QColor(18, 20, 26, 238))
            p.drawRoundedRect(rect, 8, 8)
            p.save()
            p.translate(rect.left() + 14, rect.top() + 12)
            doc.drawContents(p)
            p.restore()

        hint = {SELECT: HINT_SELECT, READING: HINT_READING,
                TEXT: HINT_TEXT if self._lines else HINT_EMPTY}[self._state]
        self._pill(p, hint, 14)
        if self._toast:
            self._pill(p, self._toast, self.height() - 50, QColor(27, 107, 47, 235))
        p.end()

    def _paint_layer(self, p: QPainter):
        for li, line in enumerate(self._lines):
            p.fillRect(line.patch, line.bg)
            fm = QFontMetrics(line.font)
            if line.vertical:
                self._paint_vertical(p, li, line, fm)
                continue
            for wi, w in enumerate(line.words):
                span = QRectF(w.x0 - 1, line.patch.top() + 1, w.x1 - w.x0 + 2, line.patch.height() - 2)
                span_key = (line.offset + w.start, line.offset + w.end)
                if span_key in self._marks:
                    p.fillRect(span, MARK)
                elif span_key in self._pending:
                    p.fillRect(span, PENDING)
                if self._hover_key == (li, wi):
                    p.fillRect(span, HOVER)
                    p.setPen(QPen(line.fg, 1))
                    y = span.bottom() - 1
                    p.drawLine(QPointF(span.left(), y), QPointF(span.right(), y))
            p.setFont(line.font)
            p.setPen(line.fg)
            baseline = line.rect.center().y() + (fm.ascent() - fm.descent()) / 2
            p.drawText(QPointF(line.rect.left(), baseline), line.text)

    def _paint_vertical(self, p: QPainter, li: int, line, fm):
        r = line.rect
        for wi, w in enumerate(line.words):
            span = QRectF(r.left() - 1, w.x0, r.width() + 2, w.x1 - w.x0)
            span_key = (line.offset + w.start, line.offset + w.end)
            if span_key in self._marks:
                p.fillRect(span, MARK)
            elif span_key in self._pending:
                p.fillRect(span, PENDING)
            if self._hover_key == (li, wi):
                p.fillRect(span, HOVER)
                p.setPen(QPen(line.fg, 1))
                p.drawLine(QPointF(span.right() - 1, span.top()), QPointF(span.right() - 1, span.bottom()))
        p.setFont(line.font)
        p.setPen(line.fg)
        for i, ch in enumerate(line.text):
            ch = "｜" if ch == "ー" else ch                      # la barra larga va vertical
            cell = QRectF(r.left(), r.top() + i * line.cell, r.width(), line.cell)
            p.drawText(cell, Qt.AlignCenter, ch)

    def _pill(self, p: QPainter, text: str, y: int, color: QColor = QColor(18, 20, 26, 225)):
        font = QFont(self.font())
        font.setPixelSize(14)
        fm = QFontMetrics(font)
        text = fm.elidedText(text, Qt.ElideRight, self.width() - 60)
        w, h = fm.horizontalAdvance(text) + 28, fm.height() + 12
        rect = QRectF((self.width() - w) / 2, y, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setFont(font)
        p.setPen(QColor(245, 245, 245))
        p.drawText(rect, Qt.AlignCenter, text)
