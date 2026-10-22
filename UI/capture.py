"""Captura de pantalla (el selector de región y la capa de texto están en overlay.py)."""
import mss
import numpy as np
from PyQt5.QtGui import QImage, QPixmap


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
