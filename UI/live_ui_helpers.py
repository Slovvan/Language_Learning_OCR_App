"""Utilidades de Windows para el modo en vivo."""
import sys

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QWidget


def exclude_from_capture(widget) -> bool:
    """Que la ventana no salga en las capturas de pantalla (Windows 10 versión 2004 o superior). Así la región, aunque
    dibuje texto encima del juego, deja ver el juego por debajo a la lectura automática. False si no se pudo."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        WDA_EXCLUDEFROMCAPTURE = 0x11
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), WDA_EXCLUDEFROMCAPTURE))
    except Exception:  # noqa: BLE001
        return False


class CaptureShield(QObject):
    """Filtro de eventos de la aplicación: toda ventana de la app (principal, diálogos, menús, avisos) se excluye de las
    capturas de pantalla en cuanto se muestra, para que el OCR no lea la propia interfaz (p. ej. Configuración).
    Se salta las ventanas de `skip` (la capa congelada, que sí debe verse). `ok` dice si Windows lo aceptó."""

    def __init__(self, exclude_fn=None, skip=()):
        super().__init__()
        self._exclude = exclude_fn or exclude_from_capture
        self._skip = list(skip)
        self.ok = None

    def add_skip(self, widget):
        self._skip.append(widget)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() and not any(obj is w for w in self._skip):
            try:
                self.ok = bool(self._exclude(obj))
            except Exception:  # noqa: BLE001
                self.ok = False
        return False
