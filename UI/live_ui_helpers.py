"""Utilidades de Windows para el modo en vivo."""
import sys


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
