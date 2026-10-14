"""Captura de pantalla y selector de región (congela la pantalla para elegir)."""
import mss
import numpy as np
from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QWidget


def grab_screen(monitor_index: int = 1) -> np.ndarray:
    """Captura un monitor completo y lo devuelve como array BGR (alto, ancho, 3).

    monitor_index=1 es el monitor principal en mss.
    """
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[monitor_index])
        return np.ascontiguousarray(np.array(shot)[:, :, :3])  # BGRA -> BGR


def bgr_to_qpixmap(frame: np.ndarray) -> QPixmap:
    rgb = np.ascontiguousarray(frame[:, :, ::-1])
    h, w, _ = rgb.shape
    qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class RegionSelector(QWidget):
    """Overlay a pantalla completa: arrastra el ratón para elegir la región.

    Emite `selected(np.ndarray)` con el recorte (BGR) o `cancelled()` con Esc /
    clic derecho / selección demasiado pequeña.
    """

    selected = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(self, frame: np.ndarray):
        super().__init__()
        self._frame = frame
        self._pixmap = bgr_to_qpixmap(frame)
        self._origin = None
        self._current = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setCursor(Qt.CrossCursor)

    # --- ciclo de vida ---
    def start(self):
        self.showFullScreen()
        self.activateWindow()
        self.setFocus()

    def _finish(self, rect: QRect | None):
        self.hide()
        if rect is None or rect.width() < 5 or rect.height() < 5:
            self.cancelled.emit()
            return
        # Convertir de píxeles lógicos (widget) a píxeles reales (captura)
        sx = self._frame.shape[1] / self.width()
        sy = self._frame.shape[0] / self.height()
        x0, y0 = int(rect.left() * sx), int(rect.top() * sy)
        x1, y1 = int(rect.right() * sx), int(rect.bottom() * sy)
        self.selected.emit(self._frame[y0:y1, x0:x1].copy())

    # --- eventos ---
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._finish(None)
            return
        self._origin = e.pos()
        self._current = e.pos()
        self.update()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._current = e.pos()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._origin is not None:
            self._finish(QRect(self._origin, e.pos()).normalized())
            self._origin = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._finish(None)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._pixmap)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))  # oscurece todo

        if self._origin is not None and self._current is not None:
            sel = QRect(self._origin, self._current).normalized()
            # Redibuja la zona elegida sin oscurecer
            kx = self._pixmap.width() / self.width()
            ky = self._pixmap.height() / self.height()
            src = QRect(int(sel.x() * kx), int(sel.y() * ky),
                        int(sel.width() * kx), int(sel.height() * ky))
            p.drawPixmap(sel, self._pixmap, src)
            p.setPen(QPen(QColor(0, 200, 255), 2))
            p.drawRect(sel)