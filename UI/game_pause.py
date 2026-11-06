"""Pausa del juego: pulsa una tecla (la de pausa del propio juego) al congelar y otra vez al descongelar.

Funciona como si la pulsaras tú: la recibe la ventana que tenga el foco (el juego, si no has hecho clic en la región o el
panel). Es una tecla que alterna: si el juego ya estaba en pausa, se reanuda. Solo Windows y con pynput; sin él no hace nada."""
import os
import sys

from UI.hotkeys import _split, normalize_global

_SPECIAL = {"esc": "esc", "enter": "enter", "space": "space", "tab": "tab", "backspace": "backspace", "pause": "pause",
            "delete": "delete", "insert": "insert", "home": "home", "end": "end", "page_up": "page_up",
            "page_down": "page_down", "up": "up", "down": "down", "left": "left", "right": "right",
            "scroll_lock": "scroll_lock", "print_screen": "print_screen", "menu": "menu"}


def _to_pynput(keyboard, token: str):
    """Una pieza del atajo («ctrl», «f9», «p», «99») como tecla de pynput, o None."""
    if token in ("ctrl", "alt", "shift"):
        return getattr(keyboard.Key, token)
    if token == "cmd":
        return keyboard.Key.cmd
    if token in _SPECIAL:
        return getattr(keyboard.Key, _SPECIAL[token], None)
    if token.startswith("f") and token[1:].isdigit():
        return getattr(keyboard.Key, token, None)
    if token.isdigit() and 96 <= int(token) <= 111:
        return keyboard.KeyCode.from_vk(int(token))
    if len(token) == 1:
        return keyboard.KeyCode.from_char(token)
    return None


def send_combo(combo: str) -> bool:
    """Pulsa y suelta el atajo (formato pynput, de 1 a 3 teclas). False si no se pudo."""
    combo = normalize_global(combo or "")
    if not combo:
        return False
    try:
        from pynput import keyboard
        ctl = keyboard.Controller()
        mods, keys = _split(combo)
        pieces = [_to_pynput(keyboard, t) for t in mods + keys]
        if not pieces or any(p is None for p in pieces):
            return False
        for p in pieces:
            ctl.press(p)
        for p in reversed(pieces):
            ctl.release(p)
        return True
    except Exception:  # noqa: BLE001
        return False


# --- a qué ventana le llega la tecla (solo Windows) ---
def game_window_at(x: int, y: int):
    """Ventana de otra aplicación (el juego) que está bajo el punto (x, y) en píxeles reales, o None.
    Recorre las ventanas de arriba abajo y se salta las nuestras, las ocultas, las herramientas y el escritorio."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        mine, found = os.getpid(), []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == mine:
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if not (rect.left <= x < rect.right and rect.top <= y < rect.bottom) or rect.right - rect.left < 100:
                return True
            if user32.GetWindowLongW(hwnd, -20) & (0x80 | 0x08000000):       # WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
                return True
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            if name.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
                return True
            found.append(int(hwnd))
            return False
        user32.EnumWindows(proc(visit), 0)
        return found[0] if found else None
    except Exception:  # noqa: BLE001
        return None


def focus_window(hwnd) -> bool:
    """Pone `hwnd` en primer plano. True si hubo que cambiarlo (conviene esperar un instante antes de pulsar teclas)."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        if int(user32.GetForegroundWindow()) == int(hwnd) or not user32.IsWindow(hwnd):
            return False
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False
