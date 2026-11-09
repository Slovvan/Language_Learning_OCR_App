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

Para leer a mano, la región y el panel se vuelven invisibles un instakkkknte (así no se lee el propio texto) y se capturan solo esos píxeles.
"""
import html as _html
import time
from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication, QColor, QFont, QFontMetrics, QPainter, QPen, QRegion
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QGridLayout, QPushButton, QSizeGrip, QSizePolicy, QTextBrowser, QWidget,
                             QVBoxLayout)

from UI.app_settings import get_number, hotkey_label, key_action, load_keys
from UI.game_pause import focus_window, game_window_at, send_combo
from UI.live_options import checkbox as option_checkbox, OPTIONS
from UI.capture import bgr_to_qpixmap, grab_region, grab_screen
from UI.ocr_modes import ModeBar
from UI.live import LiveController, signature, changed_fraction
from UI.live_ui_helpers import exclude_from_capture
from UI.overlay import DIM, MARK, PENDING, sample_colors, uniform_font
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
    def __init__(self, text, offset, rect, patch, bg, fg, font, words, vertical=False, cell=0.0):
        self.text, self.offset, self.rect, self.patch = text, offset, rect, patch
        self.bg, self.fg, self.font, self.words = bg, fg, font, words
        self.vertical, self.cell = vertical, cell     # columna de texto vertical: cada carácter en su celda, de arriba abajo


class FreezeScreen(QWidget):
    """Pantalla entera congelada: una captura a pantalla completa, por debajo de la región y del panel.
    No recibe el mouse (los clics pasan al juego) ni toma el foco. WA_TransparentForMouseEvents solo vale dentro de Qt:
    en Windows hace falta WindowTransparentForInput (clics de verdad a través de la ventana) y WindowDoesNotAcceptFocus
    (no se activa al hacer clic). Si no, un clic fuera de la región activaba esta ventana, que se subía encima de la
    región y del panel y los tapaba: no se podía descongelar."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pix = None
        self._hole = QRect()                 # la región (en coordenadas de esta ventana): ahí no se oscurece

    def show_image(self, image, geometry: QRect, hole: QRect = None):
        self._pix = bgr_to_qpixmap(image)
        self._hole = QRect(hole) if hole is not None else QRect()
        self.setGeometry(geometry)
        self.show()
        self.lower()                          # por debajo de la región y del panel, que también están «siempre encima»

    def clear(self):
        self._pix = None
        self._hole = QRect()
        self.hide()

    def paintEvent(self, _):
        if self._pix is not None:
            p = QPainter(self)
            p.drawPixmap(self.rect(), self._pix)
            # igual que la captura congelada: se oscurece todo menos la región, que queda a la vista
            if self._hole.isValid() and not self._hole.isEmpty():
                p.setClipRegion(QRegion(self.rect()).subtracted(QRegion(self._hole)))
            p.fillRect(self.rect(), DIM)
            p.end()


class RegionLayer(QWidget):
    """La región fija. Siempre recibe el mouse. Vacía solo se ve su marco; tras leer dibuja el texto sobre el original."""
    word_context = pyqtSignal(str, int)
    word_hovered = pyqtSignal(str, str, QPoint)
    word_anchor = pyqtSignal(QRect, bool, bool)   # rect global de la palabra, si el popup va arriba y si va al costado (vertical)
    hover_ended = pyqtSignal()
    wheel_moved = pyqtSignal(int)
    word_clicked = pyqtSignal(str, str, int, str)       # palabra, compuesto, posición, texto completo
    key_pressed = pyqtSignal(int, int)                  # tecla y modificadores, con la región en foco
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
        self._frozen_image = None           # la misma imagen (BGR), para comparar al descongelar
        self._frozen = None                 # QPixmap de la región congelada (se dibuja opaca bajo el texto)
        self.bounds = None                  # límites (la pantalla) para mover la región
        self._drag = None                   # (zona, posición global inicial, región inicial)
        self._lines = []
        self._v_cell = 0.0                  # alto de celda del texto vertical leído (0 = el texto no es vertical)
        self._translation = ""              # última traducción (para dibujarla sobre la región si se pide)
        self.opacity = 1.0                  # opacidad del fondo del texto dibujado
        self.over = False                   # «traducción sobre la región»: se dibuja ella y no el texto reconocido
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
        self._lines, self._fallback, self._translation, self._v_cell = [], "", "", 0.0
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
        self._frozen_image = image
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
        self.key_pressed.emit(e.key(), int(e.modifiers()))

    def set_result(self, res, image, region: QRect):
        """Dibuja `res` (ocr_engine.OcrResult de `image`, el recorte de la región) sobre el original."""
        self.clear()
        h, w = image.shape[:2]
        sx, sy = w / max(1, region.width()), h / max(1, region.height())
        boxes = getattr(res, "boxes", None) or []
        if not boxes or len(boxes) < len(res.lines):
            self._fallback_colors(image)
            return                                             # la traducción llega después y se dibuja entonces
        if getattr(res, "vertical", False):                    # japonés vertical: columnas dibujadas en su sitio, como en la captura
            self._fallback_colors(image)
            offset = 0
            for (text, _score), box in zip(res.lines, boxes):
                rect = QRectF(box[0] / sx, box[1] / sy, (box[2] - box[0]) / sx, (box[3] - box[1]) / sy)
                bg, fg = sample_colors(image, box)
                self._add_vertical(text, rect, bg, fg, offset)
                offset += len(text) + 1
            cells = sorted(l.cell for l in self._lines)
            self._v_cell = cells[len(cells) // 2] if cells else 0.0
            self.update()
            return
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

    def _add_vertical(self, text, rect, bg, fg, offset):
        cell = max(8.0, min(rect.width(), rect.height() / max(1, len(text))))
        font = QFont(self.font())
        font.setStyleStrategy(QFont.PreferAntialias)
        font.setHintingPreference(QFont.PreferNoHinting)
        font.setPixelSize(max(8, int(cell * 0.82)))
        box = QRectF(rect.center().x() - cell / 2, rect.top(), cell, max(cell * len(text), rect.height()))
        words = [(st, en, wd, c, box.top() + st * cell, box.top() + en * cell) for st, en, wd, c in iter_words(text)]
        self._lines.append(_Line(text, offset, box, box.adjusted(-3, -2, 3, 2), bg, fg, font, words, True, cell))

    def _fallback_colors(self, image):
        h, w = image.shape[:2]
        self._bg, self._fg = sample_colors(image, (0, 0, w, h))

    def set_translation_fallback(self, text: str):
        """Sin posiciones del texto, la traducción se dibuja ocupando la región."""
        self._translation = text
        if not self._lines:
            self._fallback = text
        self.update()

    def set_opacity(self, value: float):
        self.opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def set_over(self, on: bool):
        self.over = bool(on)
        self._end_hover()
        self.update()

    def set_marks(self, ranges, pending=()):
        self._marks, self._pending = set(ranges), set(pending)
        self.update()

    # ------------------------------------------------------------------ palabras
    def _showing_translation(self) -> bool:
        return self.over and bool(self._translation)

    def _hit(self, pos):
        if not self.text_visible or self.editing or self.stale or self._showing_translation():
            return None
        for li, line in enumerate(self._lines):
            if line.patch.contains(QPointF(pos)):
                coord = pos.y() if line.vertical else pos.x()
                for wi, w in enumerate(line.words):
                    if w[4] - 1 <= coord <= w[5] + 1:
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
            r = (QRectF(line.patch.left(), w[4], line.patch.width(), w[5] - w[4]) if line.vertical
                 else QRectF(w[4], line.patch.top(), w[5] - w[4], line.patch.height())).toAlignedRect()
            self._hover_pending = (line.text, w[0], w[2], w[3], e.globalPos(),
                                   QRect(self.mapToGlobal(r.topLeft()), r.size()), bool(line.vertical))
            self._timer.start()
        self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
        self.update()

    def _emit_hover(self):
        if self._hover_pending is None:
            return
        text, start, word, compound, pos, grect, vertical = self._hover_pending
        self._hover_shown = True
        screen = QApplication.screenAt(grect.center()) or QApplication.primaryScreen()
        above = self.geometry().center().y() > screen.geometry().center().y()   # región en la mitad inferior
        self.word_anchor.emit(grect, above, vertical)
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

    def _vertical_layout(self, text: str):
        """(celda, columnas): la traducción en columnas de derecha a izquierda, con celdas como las del original
        (más pequeñas si no cabe). Cada columna es un trozo de texto."""
        w, h = self.width() - 12, self.height() - 12
        cell = max(8, int(self._v_cell))
        while True:
            rows = max(1, int(h // cell))
            cols = []
            for para in text.split("\n"):
                para = para.strip()
                while para:
                    cut = rows
                    if len(para) > rows and para[rows] != " " and " " in para[:rows]:
                        cut = para.rindex(" ", 0, rows)           # no se parte una palabra si cabe entera en otra columna
                    cols.append(para[:cut].rstrip())
                    para = para[cut:].lstrip()
            if len(cols) * cell <= w or cell <= 8:
                return cell, cols
            cell -= 1

    def _translation_font(self, text: str, box: QRectF) -> QFont:
        """Letra de la traducción dibujada encima: nunca mayor que la del texto original (la mediana de sus líneas) y,
        si no cabe en la región, se reduce hasta que quepa."""
        sizes = sorted(l.font.pixelSize() for l in self._lines if l.font.pixelSize() > 0)
        cap = sizes[len(sizes) // 2] if sizes else int(self.height() * 0.3)
        size = max(9, min(cap, 40))
        font = QFont(self.font())
        font.setPixelSize(size)
        while size > 9:
            font.setPixelSize(size)
            need = QFontMetrics(font).boundingRect(box.toRect(), Qt.TextWordWrap | Qt.AlignLeft, text).height()
            if need <= box.height():
                break
            size -= 1
        font.setPixelSize(size)
        return font

    def _paint_vertical_translation(self, p, text: str):
        cell, cols = self._vertical_layout(text)
        font = QFont(self.font())
        font.setStyleStrategy(QFont.PreferAntialias)
        font.setHintingPreference(QFont.PreferNoHinting)
        font.setPixelSize(max(8, int(cell * 0.82)))
        p.setFont(font)
        p.setPen(self._fg)
        x = self.width() - 6 - cell                              # la primera columna va a la derecha
        for col in cols:
            for i, ch in enumerate(col):
                p.drawText(QRectF(x, 6 + i * cell, cell, cell), Qt.AlignCenter, ch)
            x -= cell

    def _paint_vertical(self, p, li, line):
        r = line.rect
        for wi, w in enumerate(line.words):
            span = QRectF(r.left() - 1, w[4], r.width() + 2, w[5] - w[4])
            key = (line.offset + w[0], line.offset + w[1])
            if key in self._marks:
                p.fillRect(span, MARK)
            elif key in self._pending:
                p.fillRect(span, PENDING)
            if self._hover_key == (li, wi):
                p.fillRect(span, QColor(255, 255, 255, 70))
                p.setPen(QPen(line.fg, 1))
                p.drawLine(QPointF(span.right() - 1, span.top()), QPointF(span.right() - 1, span.bottom()))
        p.setFont(line.font)
        p.setPen(line.fg)
        for i, ch in enumerate(line.text):
            ch = "｜" if ch == "ー" else ch                      # la barra larga va vertical
            p.drawText(QRectF(r.left(), r.top() + i * line.cell, r.width(), line.cell), Qt.AlignCenter, ch)

    # ------------------------------------------------------------------ dibujo
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))             # casi invisible, pero recibe el mouse
        if self._frozen is not None:
            p.drawPixmap(self.rect(), self._frozen)
        over = self._showing_translation()
        shown = self._translation if over else self._fallback
        if shown and self.text_visible and not self.editing and not self.stale:
            p.fillRect(self.rect(), QColor(self._bg.red(), self._bg.green(), self._bg.blue(), int(245 * self.opacity)))
            if over and self._v_cell:                            # el original era vertical: la traducción también, sin pasarse de su tamaño
                self._paint_vertical_translation(p, shown)
                shown = ""
        if shown and self.text_visible and not self.editing and not self.stale:
            box = QRectF(self.rect()).adjusted(10, 6, -10, -6)
            p.setFont(self._translation_font(shown, box))
            p.setPen(self._fg)
            p.drawText(box, Qt.AlignVCenter | Qt.TextWordWrap, shown)
        for li, line in enumerate(self._lines if self.text_visible and not self.editing and not self.stale and not over else []):
            bg = QColor(line.bg)
            bg.setAlpha(int(255 * self.opacity))
            p.fillRect(line.patch, bg)
            fm = QFontMetrics(line.font)
            if line.vertical:
                self._paint_vertical(p, li, line)
                continue
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


QUICK_STYLE = """
QuickOptions { background: rgb(24, 27, 35); border: 1px solid rgba(255,255,255,90); border-radius: 8px; }
QLabel { color: #9aa3b2; font-size: 11px; }
QLabel#sect { color: #7fa3b8; font-size: 11px; font-weight: bold; padding-top: 4px; }
QCheckBox { color: #e8eef7; font-size: 13px; spacing: 7px; padding: 2px 0; }
QPushButton { color: #f2f2f2; background: rgba(255,255,255,18); border: 1px solid rgba(255,255,255,50);
    border-radius: 5px; padding: 3px 9px; font-size: 12px; }
QPushButton:hover { background: rgba(255,255,255,45); }
"""
QUICK_SECTIONS = (("read", "Lectura"), ("panel", "Panel"))


class QuickOptions(QFrame):
    """Menú de la tuerca ⚙: las casillas del modo en vivo, a mano sin abrir Configuración. Aparece al pasar el mouse por la
    tuerca, no roba el foco y se cierra solo poco después de que el mouse se aleja."""
    settings_requested = pyqtSignal()
    entered = pyqtSignal()
    left = pyqtSignal()

    def __init__(self, model):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("QuickOptions")
        self.setStyleSheet(QUICK_STYLE)
        self.boxes = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(1)
        for group, title in QUICK_SECTIONS:
            lab = QLabel(title.upper())
            lab.setObjectName("sect")
            lay.addWidget(lab)
            for key, _text, _tip, _default, g in OPTIONS:
                if g == group:
                    box = option_checkbox(model, key, self)
                    box.setFocusPolicy(Qt.NoFocus)
                    self.boxes[key] = box
                    lay.addWidget(box)
        more = QPushButton("Más ajustes…")
        more.setFocusPolicy(Qt.NoFocus)
        more.setToolTip("Abre Configuración ▸ Modo en vivo (tecla de pausa, tiempos, teclas, perfiles)")
        more.clicked.connect(self.settings_requested)
        lay.addSpacing(6)
        lay.addWidget(more)
        self.adjustSize()

    def enterEvent(self, e):
        self.entered.emit()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.left.emit()
        super().leaveEvent(e)

    def show_near(self, anchor: QRect):
        """Se coloca bajo la tuerca (o encima si abajo no cabe), sin salirse de la pantalla."""
        self.adjustSize()
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        w, h = self.width(), self.height()
        x = min(max(geo.left(), anchor.right() - w + 1), geo.right() - w + 1)
        y = anchor.bottom() + 4
        if y + h > geo.bottom():
            y = max(geo.top(), anchor.top() - h - 4)
        self.move(x, y)
        self.show()
        self.raise_()


PANEL_STYLE = """
LivePanel { background: rgb(18, 20, 26); border: 1px solid rgba(255,255,255,70); border-radius: 8px; }
QLabel#head { color: #9aa3b2; font-size: 12px; }
QTextBrowser { background: transparent; border: none; color: #b9e6ff; font-size: %PX%px; }
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
    copy_requested = pyqtSignal()
    close_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    entry_deleted = pyqtSignal(int)              # ✕ de una entrada del historial
    size_changed = pyqtSignal(QSize)             # el usuario le cambió el tamaño
    key_pressed = pyqtSignal(int, int)
    entered = pyqtSignal()
    left = pyqtSignal()
    settings_requested = pyqtSignal()            # «Más ajustes…» del menú ⚙

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("LivePanel")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._px = 16
        self.setStyleSheet(PANEL_STYLE.replace("%PX%", str(self._px)))
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
        self.btn_copy = QPushButton("Copiar")
        self.btn_copy.setToolTip("Copia la última lectura al portapapeles (la traducción; con «Original en el panel», también el original)")
        self.btn_close = QPushButton("✕")
        self.btn_close.setToolTip("Cierra el panel (T o Ctrl+Alt+T lo vuelve a abrir)")
        self.btn_close.setFixedWidth(30)
        self.btn_close.setFocusPolicy(Qt.NoFocus)
        self.btn_close.clicked.connect(self.close_requested)
        self.btn_gear = QPushButton("⚙")
        self.btn_gear.setObjectName("gear")
        self.btn_gear.setToolTip("Opciones del modo en vivo (pasa el mouse; clic para dejarlo fijo)")
        self.btn_gear.setFixedWidth(30)
        self.btn_gear.setFocusPolicy(Qt.NoFocus)
        self.btn_gear.installEventFilter(self)
        self.quick = None                            # el menú de la tuerca (lo crea set_options)
        self._quick_pinned = False
        self._quick_timer = QTimer(self)
        self._quick_timer.setSingleShot(True)
        self._quick_timer.setInterval(400)
        self._quick_timer.timeout.connect(self._quick_close)
        self.btn_gear.clicked.connect(self._gear_clicked)
        self.btn_clear = QPushButton("Limpiar")
        self.btn_clear.setToolTip("Borra todo lo que muestra el panel (el registro principal de la app no se toca)")
        for b in (self.btn_copy, self.btn_clear):
            b.setFocusPolicy(Qt.NoFocus)
        self.btn_copy.clicked.connect(self.copy_requested)
        self.btn_clear.clicked.connect(self.clear_requested)
        self.head.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._anchor)
        self.view.setReadOnly(True)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setFocusPolicy(Qt.NoFocus)
        self.view.setPlaceholderText("La traducción aparece aquí al pulsar «Leer».")
        self.grip = QSizeGrip(self)
        row = QGridLayout()
        row.setHorizontalSpacing(5)
        row.setVerticalSpacing(4)
        for i, b in enumerate((self.btn_read, self.btn_auto, self.btn_toggle, self.btn_edit, self.btn_freeze, self.btn_stop)):
            row.addWidget(b, i // 3, i % 3)
        row.addWidget(self.grip, 1, 3, Qt.AlignBottom | Qt.AlignRight)
        row.setColumnStretch(3, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(4)
        head_row = QHBoxLayout()
        head_row.setSpacing(5)
        head_row.addWidget(self.head, 1)
        head_row.addWidget(self.btn_copy)
        head_row.addWidget(self.btn_clear)
        head_row.addWidget(self.btn_gear)
        head_row.addWidget(self.btn_close)
        lay.addLayout(head_row)
        self.mode_bar = ModeBar()                    # modos de OCR del japonés (se esconde con otros idiomas)
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.addWidget(self.mode_bar)
        mode_row.addStretch(1)
        lay.addLayout(mode_row)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        self._placing = False
        # que los botones siempre quepan enteros (3 por fila)
        cols = [max(self.btn_read.sizeHint().width(), self.btn_edit.sizeHint().width()),
                max(self.btn_auto.sizeHint().width(), self.btn_freeze.sizeHint().width()),
                max(self.btn_toggle.sizeHint().width(), self.btn_stop.sizeHint().width())]
        need = sum(cols) + 2 * 5 + self.grip.sizeHint().width() + 5 + 20 + 40
        self.setMinimumSize(max(PANEL_MIN.width(), need), PANEL_MIN.height() + 30)

    # --- tuerca ⚙: menú con las casillas ---
    def set_options(self, model):
        if self.quick is not None:
            self.quick.deleteLater()
        self.quick = QuickOptions(model)
        self.quick.entered.connect(self._quick_timer.stop)
        self.quick.left.connect(self._quick_leave)
        self.quick.settings_requested.connect(self._quick_more)
        self.quick.settings_requested.connect(self.settings_requested)

    def quick_visible(self) -> bool:
        return self.quick is not None and self.quick.isVisible()

    def busy(self) -> bool:
        """¿El mouse está en el panel o en su menú ⚙? (para no esconder el panel mientras se usa)"""
        return self.underMouse() or self.quick_visible() or self._drag is not None

    def eventFilter(self, obj, ev):
        if obj is self.btn_gear:
            if ev.type() == QEvent.Enter:
                self._quick_open()
            elif ev.type() == QEvent.Leave:
                self._quick_leave()
        return super().eventFilter(obj, ev)

    def _quick_open(self):
        if self.quick is None:
            return
        self._quick_timer.stop()
        if not self.quick.isVisible():
            top_left = self.btn_gear.mapToGlobal(QPoint(0, 0))
            self.quick.show_near(QRect(top_left, self.btn_gear.size()))

    def _quick_leave(self):
        if not self._quick_pinned:
            self._quick_timer.start()

    def _quick_close(self):
        if self.quick is not None and not self._quick_pinned:
            if self.quick.underMouse() or self.btn_gear.underMouse():
                return
            self.quick.hide()
            self.left.emit()                         # el panel puede plegarse / esconderse ahora

    def _gear_clicked(self):
        self._quick_pinned = not self._quick_pinned
        if self._quick_pinned:
            self._quick_open()
        elif self.quick is not None:
            self._quick_timer.start()
        self.btn_gear.setStyleSheet("background: rgba(0,200,255,70);" if self._quick_pinned else "")

    def _quick_more(self):
        self._quick_pinned = False
        self.btn_gear.setStyleSheet("")
        if self.quick is not None:
            self.quick.hide()

    def hideEvent(self, e):
        self._quick_pinned = False
        self.btn_gear.setStyleSheet("")
        if self.quick is not None:
            self.quick.hide()
        super().hideEvent(e)

    def set_user_size(self, size):
        self._user_size = size if size is not None and size.width() >= PANEL_MIN.width() \
            and size.height() >= PANEL_MIN.height() else None

    def default_size(self, region: QRect) -> QSize:
        return QSize(max(self.minimumWidth(), min(700, region.width())), 190)

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
        self.key_pressed.emit(e.key(), int(e.modifiers()))

    def enterEvent(self, e):
        self.entered.emit()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.left.emit()
        super().leaveEvent(e)

    def set_font_px(self, px: int):
        self._px = int(px)
        self.setStyleSheet(PANEL_STYLE.replace("%PX%", str(self._px)))

    def _anchor(self, url):
        text = url.toString()
        if text.startswith("del:") and text[4:].isdigit():
            self.entry_deleted.emit(int(text[4:]))

    def set_entries(self, entries, show_original: bool, deletable: bool):
        """Muestra las entradas ([{id, original, translation}], la más nueva primero) con la más nueva ABAJO y destacada."""
        rows = []
        ordered = list(reversed(entries))
        for i, e in enumerate(ordered):
            latest = i == len(ordered) - 1
            body = []
            if show_original:
                color, size = ("#9fb0c6", self._px - 2) if latest else ("#6f7a8a", self._px - 4)
                body.append(f"<div style='color:{color}; font-size:{size}px'>"
                            + _html.escape(e["original"]).replace("\n", "<br>") + "</div>")
            tr = _html.escape(e["translation"]).replace("\n", "<br>") if e["translation"] else "<i style='color:#6f7a8a'>…</i>"
            color, size = ("#d6f1ff", self._px + 1) if latest else ("#7fa3b8", self._px - 2)
            body.append(f"<div style='color:{color}; font-size:{size}px'>{tr}</div>")
            cross = ("<a href='del:%d' style='color:#8a93a3; text-decoration:none; font-size:%dpx'>&#10005;</a>" % (e["id"], self._px)
                     if deletable else "")
            bg = " bgcolor='#223247'" if latest else ""
            pad = 8 if latest else 3
            rows.append(f"<table width='100%' cellspacing='0' cellpadding='{pad}'{bg}><tr>"
                        f"<td>{''.join(body)}</td>"
                        f"<td width='34' align='center' valign='top'>{cross}</td></tr></table>")
            if not latest:
                rows.append("<div style='font-size:5px'>&nbsp;</div>")
        if len(ordered) > 1:                        # más aire entre lo anterior y la lectura actual
            rows.insert(len(rows) - 1, "<div style='font-size:10px'>&nbsp;</div><hr>")
        self.view.setHtml("".join(rows))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

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


class PanelTab(QWidget):
    """La «viñeta»: una pestaña pequeña junto a la región; al pasar el mouse se abre el panel."""
    entered = pyqtSignal()
    SIZE = QSize(124, 26)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE)
        self._new = False
        self.setCursor(Qt.PointingHandCursor)

    def set_new(self, new: bool):
        if new != self._new:
            self._new = new
            self.update()

    def place(self, region: QRect):
        screen = QApplication.screenAt(region.center()) or QApplication.primaryScreen()
        geo = screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self.setGeometry(compute_panel_rect(region, self.SIZE, geo))

    def enterEvent(self, e):
        self.entered.emit()
        super().enterEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 80), 1))
        p.setBrush(QColor(18, 20, 26, 235))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        p.setPen(QColor(185, 230, 255) if self._new else QColor(154, 163, 178))
        font = QFont(self.font())
        font.setPixelSize(13)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignCenter, ("● " if self._new else "") + "Traducción ▾")
        p.end()


class LiveMode(QObject):
    active_changed = pyqtSignal(bool)
    view_changed = pyqtSignal()              # cambió lo que se ve: texto sobre la región o panel (los botones de la ventana principal)
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
        self.options = win.live_options                      # las casillas: Configuración y tuerca del panel
        self.auto = self.options.get("live_auto")            # lectura automática (se recuerda)
        self.capture_safe = False                            # ¿la región se excluye de las capturas? (hace falta para vigilar)
        self.kind = "manual"                                 # quién pidió la lectura en curso: «manual» o «auto»
        self.skip = False                                    # esa lectura se descarta (vacía o repetida)
        self.layer = RegionLayer()
        self.freezer = FreezeScreen()
        self._full = None                    # captura de la pantalla entera tomada junto con la lectura
        self._freeze_paused = False
        self._force_freeze = False           # la lectura en curso la pidió «Congelar»: al terminar se congela
        self.panel = LivePanel()
        self.tab = PanelTab()
        self.tab_mode = False                # el panel es una viñeta que se abre al pasar el mouse
        self._expanded = False               # (en modo viñeta) el panel está abierto
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(700)
        self._collapse_timer.timeout.connect(self._collapse)
        self.keys = load_keys(win.settings, "live")
        self.box = None                      # (x0, y0, x1, y1) en píxeles reales
        self.rect = None                     # QRect global en píxeles lógicos
        self.active = False
        self.inflight = False                # hay una lectura en curso pedida por el modo en vivo
        self.panel_visible = True            # «T»: el panel con la traducción
        self.text_visible = True             # «O»: el texto dibujado sobre el original
        self._reading = False                # entre pulsar «Leer» y recibir la captura
        self._image = None
        self.editing = False                 # se está moviendo / estirando la región
        self.freeze_read = self.options.get("live_freeze_read")
        self.freeze_auto = self.options.get("live_freeze_auto")
        self.show_original = self.options.get("live_show_original")    # el panel muestra también el texto reconocido
        self.keep_history = self.options.get("live_history")           # el panel acumula las últimas lecturas
        self.save_log = self.options.get("live_save_log")              # cada lectura va al registro principal de la app
        self.over_region = self.options.get("live_over")               # la traducción se dibuja sobre la región
        self.panel_temp = self.options.get("live_panel_temp")          # el panel se esconde solo tras unos segundos
        self._send_key = send_combo                                    # (las pruebas lo sustituyen)
        self._game_window = game_window_at
        self._focus = focus_window
        self._key_guard_until = 0.0                                    # mientras dura, nuestras ventanas ignoran teclas
        self._sent_pause = False                                       # ¿la pausa del juego la mandamos nosotros?
        self.history = []                                        # [{id, original, translation}], la más nueva primero
        self._entry_id = 0
        self._cur = None                                         # la entrada de la lectura en curso
        self._temp_timer = QTimer(self)
        self._temp_timer.setSingleShot(True)
        self.history_max = 30
        self._temp_timer.timeout.connect(self._temp_expired)
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
        self.panel.copy_requested.connect(self.copy_last)
        self.panel.mode_bar.mode_requested.connect(self.win.set_ocr_mode)
        self.tab.entered.connect(self._expand)
        self.panel.set_options(self.options)
        self.panel.settings_requested.connect(lambda: self.win.open_settings(2))
        self.panel.left.connect(self._collapse_later)
        self.panel.close_requested.connect(self.hide_panel)
        self.panel.clear_requested.connect(self.clear_history)
        self.panel.entry_deleted.connect(self.delete_entry)
        self.layer.set_over(self.over_region)
        self.panel.btn_auto.setChecked(self.auto)
        self.options.changed.connect(self._on_option)
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
        self.apply_settings()

    # ------------------------------------------------------------------ control
    def start(self, box, rect: QRect):
        self.box = tuple(int(v) for v in box)
        self.rect = QRect(rect)
        self.active = True
        self.inflight = self._reading = False
        self.text_visible = True
        self.layer.set_frozen(None)               # nada de una sesión anterior se queda pegado
        self.freezer.clear()
        self._freeze_paused = False
        self._pause_game(False)                   # si la pantalla seguía congelada con el juego en pausa, se reanuda
        self.panel.btn_freeze.setText("Congelar (F)")
        self.layer.set_editing(False)
        self.editing = False
        self.panel.btn_edit.setText("Mover región (E)")
        self._compute_scale()
        screen = QApplication.screenAt(self.rect.center()) or QApplication.primaryScreen()
        self.layer.bounds = screen.geometry() if screen is not None else None
        self.layer.clear()
        self.layer.setGeometry(self.rect)
        self._render_panel()
        self.panel.set_note(f"En vivo · pulsa «Leer» o {hotkey_label(self.win.settings, 'read')}")
        self.panel_visible = True
        self.layer.set_text_visible(True)
        self._sync_toggle_text()
        self.panel.set_region(self.rect)
        self.layer.show()
        self._sync_panel()
        self.layer.set_stale(False)
        self.capture_safe = bool(self._exclude(self.layer))
        for extra in (self.panel, self.tab, self.freezer):
            try:
                self._exclude(extra)
            except Exception:  # noqa: BLE001
                pass
        self.skip = False
        self.kind = "manual"
        self.active_changed.emit(True)
        if self.auto:
            self.set_auto(True)

    def stop(self):
        was = self.active
        self.layer.set_editing(False)
        self.editing = False
        self._hidden_for_select = False
        self._ghost(False)
        self._set_frozen(None)
        self.controller.stop()
        self.layer.set_stale(False)
        self.active = False
        self.inflight = self._reading = False
        self.layer.clear()
        self.layer.hide()
        self.panel.hide()
        self.tab.hide()
        self._expanded = False
        if was:
            self.active_changed.emit(False)

    def has_region(self) -> bool:
        return self.box is not None

    def _on_option(self, key: str, value: bool):
        """Una casilla cambió (en Configuración, en la tuerca del panel o por un perfil)."""
        if key == "live_auto":
            if value != self.auto:
                self.set_auto(value)
        elif key == "live_freeze_read":
            self.freeze_read = value
        elif key == "live_freeze_auto":
            self.freeze_auto = value
        elif key == "live_show_original":
            self.set_panel_options(original=value)
        elif key == "live_history":
            self.set_panel_options(history=value)
        elif key == "live_save_log":
            self.set_panel_options(save_log=value)
        elif key == "live_over":
            self.set_panel_options(over=value)
        elif key == "live_panel_temp":
            self.set_panel_options(temp=value)

    # --- pausa del juego ---
    def _pause_game(self, pause: bool):
        """Al congelar pulsa la tecla de pausa del juego; al descongelar, otra vez (solo si la pulsamos nosotros)."""
        key = str(self.win.settings.value("live_pause_key", ""))
        if pause:
            if not self.options.get("live_pause_on") or self._sent_pause:
                return
            if not key:
                self.message.emit("Elige la tecla de pausa del juego en Configuración ▸ Modo en vivo.")
                return
            self._sent_pause = True
            self._press(key, on_fail=True)
        elif self._sent_pause:
            self._sent_pause = False
            self._press(key)

    def key_guard(self) -> bool:
        """¿Estamos pulsando una tecla de pausa? Entonces ni la región, ni el panel, ni los atajos globales la toman."""
        return time.monotonic() < self._key_guard_until

    def _press(self, key: str, on_fail: bool = False):
        """Pulsa `key` en el juego. Si el foco lo tiene una ventana nuestra (se hizo clic en el panel o en «Congelar»),
        antes se le devuelve el foco al juego: si no, la tecla se la comería nuestra propia ventana."""
        self._key_guard_until = time.monotonic() + 0.8
        wait = 0
        try:
            if self.box is not None:
                x0, y0, x1, y1 = self.box
                hwnd = self._game_window((x0 + x1) // 2, (y0 + y1) // 2)
                if hwnd and self._focus(hwnd):
                    wait = 120
        except Exception:  # noqa: BLE001
            wait = 0

        def go():
            ok = False
            try:
                ok = bool(self._send_key(key))
            except Exception:  # noqa: BLE001
                ok = False
            if not ok and on_fail:
                self._sent_pause = False
                self.message.emit("No se pudo pulsar la tecla de pausa (hace falta pynput en Windows).")
        if wait:
            QTimer.singleShot(wait, go)
        else:
            go()

    # --- panel: original, historial, copiar, panel temporal ---
    def set_panel_options(self, original=None, history=None, save_log=None, over=None, temp=None):
        if original is not None:
            self.show_original = bool(original)
        if history is not None:
            self.keep_history = bool(history)
        if save_log is not None:
            self.save_log = bool(save_log)
        if over is not None:
            self.over_region = bool(over)
            self.layer.set_over(self.over_region)
            self.win.words.hide_popup()
        if temp is not None:
            self.panel_temp = bool(temp)
            if not self.panel_temp:
                self._temp_timer.stop()
                self._reveal_panel()
        self._render_panel()

    def _render_panel(self):
        shown = self.history if self.keep_history else self.history[:1]
        self.panel.set_entries(shown, self.show_original, self.keep_history)

    def _new_entry(self, original: str):
        self._entry_id += 1
        self._cur = {"id": self._entry_id, "original": original, "translation": ""}
        self.history.insert(0, self._cur)
        del self.history[self.history_max:]
        self._render_panel()

    def delete_entry(self, entry_id: int):
        self.history = [e for e in self.history if e["id"] != entry_id]
        if self._cur is not None and self._cur["id"] == entry_id:
            self._cur = None
        self._render_panel()

    def clear_history(self):
        self.history = []
        self._cur = None
        self._render_panel()

    def copy_last(self):
        if not self.history:
            return
        e = self.history[0]
        text = e["translation"]
        if self.show_original:
            text = f"{e['original']}\n{text}".rstrip()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.panel.set_note("En vivo · copiado al portapapeles")

    def _reveal_panel(self):
        if self.tab_mode:
            return
        if self.active and self.panel_visible and not self.panel.isVisible() and not self._hidden_for_select:
            self.panel.show()
            self.panel.place()

    def _temp_expired(self):
        if self.tab_mode or not self.panel_temp or not self.active or self.inflight or self._reading:
            return
        if self.panel.isVisible() and self.panel.busy():
            self._temp_timer.start()                   # el mouse está encima: se espera
            return
        self.panel.hide()

    def apply_settings(self):
        """Lee los números de la configuración (ventana «Configuración») y los aplica."""
        s = self.win.settings
        self.history_max = get_number(s, "live_history_max")
        del self.history[self.history_max:]
        self._temp_timer.setInterval(get_number(s, "live_panel_hide_s") * 1000)
        self.panel.set_font_px(get_number(s, "live_panel_font"))
        self.layer.set_opacity(get_number(s, "live_text_opacity") / 100)
        self.controller.configure(sensitivity=get_number(s, "live_sensitivity"), interval_ms=300,
                                  stable_ms=get_number(s, "live_stable_ms"), min_gap_ms=get_number(s, "live_min_gap_ms"))
        self.keys = load_keys(s, "live")
        self.panel.mode_bar.set_keys(self.keys)
        tab = s.value("live_panel_tab", False) in (True, "true", "1", 1)
        if tab != self.tab_mode:
            self.tab_mode = tab
            self._expanded = False
            self._temp_timer.stop()
            self._sync_panel()
        self._render_panel()

    def set_freeze_options(self, read=None, auto=None):
        if read is not None:
            self.freeze_read = bool(read)
        if auto is not None:
            self.freeze_auto = bool(auto)

    def _grab_full(self):
        try:
            return grab_screen()
        except Exception:  # noqa: BLE001
            return None

    def _set_frozen(self, image, full=None):
        """Congela con `image` (el recorte leído) y, si hay `full`, toda la pantalla; o descongela con None.
        Primero se arregla lo que se ve y al final se pulsa la tecla de pausa: pase lo que pase con ella, la pantalla se descongela."""
        self.layer.set_frozen(image)
        self.panel.btn_freeze.setText("Descongelar (F)" if image is not None else "Congelar (F)")
        if image is not None and full is not None and self.rect is not None:
            screen = QApplication.screenAt(self.rect.center()) or QApplication.primaryScreen()
            geo = screen.geometry()
            self.freezer.show_image(full, geo, self.rect.translated(-geo.topLeft()))
            self.layer.raise_()
            if self.panel.isVisible():
                self.panel.raise_()
        else:
            self.freezer.clear()
        self._freeze_paused = image is not None     # congelado: no se vigila el cambio de texto
        self._sync_pause()
        self._pause_game(image is not None)

    def toggle_freeze(self):
        """«F» / Ctrl+Alt+F: congela la pantalla entera con la última lectura, o la descongela."""
        if not self.active or self._reading or self._hidden_for_select:
            return
        if self.editing and not self.inflight:
            self.set_editing(False)                  # «Congelar» termina la edición de la región
        if self.editing:
            return
        if self.layer.frozen or self.freezer.isVisible():
            self._unfreeze_checked()
        elif self.inflight:
            return                                   # hay una lectura en curso: ella decide si congela
        elif self._image is None or not self.layer.has_text or self.layer.stale:
            self.read_now(freeze=True)               # no hay texto vigente (o la pantalla ya cambió): se lee lo de ahora y se congela
        else:
            self._reading = True                     # un instante invisibles, para que la captura salga limpia
            self._ghost(True)
            self.win.words.hide_popup()

            def grab():
                full = self._grab_full()
                self._reading = False
                self._ghost(False)
                if self.active and self.layer.has_text and not self.editing and not self._hidden_for_select:
                    self._set_frozen(self._image, full)
            QTimer.singleShot(READ_DELAY_MS, grab)

    def _unfreeze_checked(self):
        """Descongela y comprueba que lo que se ve ahora sea lo mismo que lo congelado: si la pantalla cambió, el texto
        dibujado ya no vale (se marca como viejo) y la vigilancia vuelve a leer."""
        shown = self.layer._frozen_image
        self._set_frozen(None)
        if shown is None or self.box is None:
            return
        try:
            now = signature(self._grab(self.box))
            if changed_fraction(now, signature(shown)) > self.controller.detector.threshold:
                self.layer.set_stale(True)
                self.win.words.hide_popup()
        except Exception:  # noqa: BLE001
            pass

    def toggle_text(self):
        """«O» / Ctrl+Alt+H: muestra u oculta el texto dibujado sobre el original. La región (y el mouse) siguen."""
        if not self.active:
            return
        self.text_visible = not self.text_visible
        self.layer.set_text_visible(self.text_visible)
        self.win.words.hide_popup()
        self._sync_toggle_text()

    def hide_panel(self):
        """✕ del panel: lo cierra (en modo viñeta, solo lo pliega a la pestaña)."""
        if self.tab_mode and self._expanded:
            self._expanded = False
            self._sync_panel()
        elif self.active and self.panel_visible:
            self.toggle_panel()

    # --- panel como ventana o como viñeta ---
    def _sync_panel(self):
        want = self.active and self.panel_visible and not self._hidden_for_select
        if not want:
            self.panel.hide()
            self.tab.hide()
        elif self.tab_mode and not self._expanded:
            self.panel.hide()
            if self.rect is not None:
                self.tab.place(self.rect)
            self.tab.show()
        else:
            self.tab.hide()
            if not self.panel.isVisible():
                self.panel.show()
            self.panel.place()

    def _expand(self):
        if self.tab_mode and self.active and not self._expanded:
            self._expanded = True
            self.tab.set_new(False)
            self._sync_panel()
            self._collapse_timer.start(1500)         # si el mouse nunca llega al panel, se pliega

    def _collapse_later(self):
        if self.tab_mode and self._expanded:
            self._collapse_timer.start()

    def _collapse(self):
        if not (self.tab_mode and self._expanded):
            return
        if self.panel.busy():
            return
        self._expanded = False
        self._sync_panel()

    def toggle_panel(self):
        """«T» / Ctrl+Alt+T: muestra u oculta el panel con la traducción."""
        if not self.active:
            return
        self.panel_visible = not self.panel_visible
        self._expanded = False
        self._sync_panel()
        self._sync_toggle_text()

    def _sync_toggle_text(self):
        self.panel.btn_toggle.setText("Quitar texto (O)" if self.text_visible else "Poner texto (O)")
        self.view_changed.emit()

    def toggle_merge(self):
        """«M» / Ctrl+Alt+M: une o separa las líneas partidas y vuelve a procesar la última lectura."""
        if not self.active or self._reading or self.inflight or self._image is None or self.editing or self._hidden_for_select:
            return
        win = self.win
        win.chk_merge.setChecked(not win.chk_merge.isChecked())
        self.message.emit("Líneas unidas" if win.chk_merge.isChecked() else "Líneas separadas")
        self.kind = "manual"
        self.inflight = True
        self.controller.set_busy(True)
        win._request(self._image)

    def reread(self):
        """Vuelve a procesar la última lectura (cambió el modo de OCR)."""
        if not self.active or self._reading or self.inflight or self._image is None or self._hidden_for_select:
            return
        self.kind = "manual"
        self.skip = False
        self.inflight = True
        self.controller.set_busy(True)
        self.win._request(self._image)

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
        if not self.active or on == self.editing or self._reading or self.inflight or (on and self._hidden_for_select):
            return
        self.editing = on
        self.win.words.hide_popup()
        self.layer.set_editing(on)
        if on and (self.layer.frozen or self.freezer.isVisible() or self._freeze_paused):
            self._set_frozen(None)                 # no se mueve la región sobre una pantalla congelada (y el juego sigue)
        self._sync_pause()
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
        self._sync_panel()
        if self.controller.box is not None:
            self.controller.set_box(self.box)

    def handle_key(self, key, modifiers=0):
        """Las teclas con la región o el panel en foco (se configuran en «Configuración»)."""
        if self.key_guard():
            return                                # es la tecla de pausa que acabamos de pulsar nosotros
        action = key_action(self.keys, Qt.KeyboardModifiers(modifiers), key)
        if action is None and key == Qt.Key_Escape and not self.keys.get("stop"):
            action = "stop"                       # Esc siempre detiene, aunque se le haya quitado la tecla
        if action in ("mode_normal", "mode_kana", "mode_vertical"):
            if self.panel.mode_bar._available:
                self.win.set_ocr_mode(action[5:])
            return
        {"stop": self.stop, "panel": self.toggle_panel, "text": self.toggle_text, "merge": self.toggle_merge,
         "edit": self.toggle_edit, "auto": self.toggle_auto, "freeze": self.toggle_freeze}.get(action, lambda: None)()

    # --- elegir otra región: se esconde todo para que no salga en la captura congelada ---
    def hide_for_select(self):
        if self.active and not self._hidden_for_select:
            self._hidden_for_select = True
            self._set_frozen(None)
            self._pause_auto(True)
            self.layer.hide()
            self.panel.hide()
            self.tab.hide()

    def restore_after_select(self):
        if not self._hidden_for_select:
            return
        self._hidden_for_select = False
        if self.active:
            self.layer.show()
            self._sync_panel()
            self._pause_auto(False)

    # ------------------------------------------------------------------ lectura
    def read_now(self, freeze=False):
        if not self.active or self._reading or self.inflight or self._hidden_for_select:
            return
        self._force_freeze = bool(freeze)
        if self.editing:
            self.set_editing(False)
        self._reading = True
        self.controller.set_busy(True)
        self.win.words.hide_popup()
        self._temp_timer.stop()
        self._reveal_panel()
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
            self._force_freeze = False
            self._ghost(False)
            self.controller.set_busy(False)
            self.panel.set_note(f"En vivo · no se pudo capturar la región ({exc})")
            return
        self._reading = False
        self._image = image
        self._full = self._grab_full() if (self.freeze_read or self._force_freeze) else None
        if self.freeze_read or self._force_freeze:
            self._pause_game(True)              # con la imagen ya tomada: el juego se detiene sin esperar al OCR
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
        self.freezer.setWindowOpacity(0.0 if on else 1.0)

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
        self.options.set("live_auto", on)
        self.panel.btn_auto.setChecked(on)
        if self.active:
            if on:
                self.controller.start(self.box)
                self._sync_pause()
            else:
                self.controller.stop()
                self.layer.set_stale(False)
            self.panel.set_note(self._base_note())
        self.auto_changed.emit(on)

    def _pause_auto(self, pause: bool = True):
        """Compatibilidad: quien llama ya cambió su motivo de pausa (editando, eligiendo región...); se recalcula todo."""
        self._sync_pause()

    def _sync_pause(self):
        """La vigilancia automática está en pausa mientras haya CUALQUIER motivo: pantalla congelada, región en edición o
        elección de otra región. Se calcula siempre desde los motivos (nada de «recordar el estado anterior»: antes, editar
        con la pantalla congelada dejaba la pausa del congelado grabada y la lectura automática no volvía hasta cerrar la app)."""
        if not self.controller.active:
            return
        want = bool(self._freeze_paused or self.editing or self._hidden_for_select)
        was = self.controller.paused
        self.controller.set_paused(want)
        if was and not want:
            self.controller.detector.reset()          # al reanudar se empieza de cero: lo de antes ya no cuenta

    def _set_anchor(self, rect, above, side=False):
        popup = getattr(self.win, "popup", None)
        if popup is not None:
            popup.set_anchor(rect, above, side)

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
        self._full = self._grab_full() if self.freeze_auto else None
        if self.freeze_auto:
            self._pause_game(True)
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
            self.panel.set_note("En vivo · sin texto en la región")
            return
        self.controller.accept_text(res.text)
        self._new_entry(res.text)
        self._reveal_panel()
        self.layer.set_result(res, self._image, self.rect)
        want = self.freeze_auto if self.kind == "auto" else (self.freeze_read or self._force_freeze)
        want = want and not (self.editing or self._hidden_for_select)      # llegó mientras se editaba o se elegía región: no se congela
        full, self._full = self._full, None
        self._set_frozen(self._image if want else None, full)
        self.layer.set_stale(False)
        self.panel.set_note("En vivo · traduciendo…")
        self.refresh_marks()

    def show_translation(self, text: str):
        if not self.inflight or self.skip:
            return
        if self._cur is not None:
            self._cur["translation"] = text
        self._render_panel()
        self.panel.set_note(self._base_note())
        self.layer.set_translation_fallback(text)
        if self.tab_mode and not self._expanded:
            self.tab.set_new(True)
        if self.panel_temp:
            self._temp_timer.start()

    def finished(self):
        self.inflight = False
        self._force_freeze = False
        if not self.layer.frozen:                 # la lectura terminó sin congelar nada (vacía, repetida, con error): se reanuda el juego
            self._pause_game(False)
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
        self.freezer.close()
