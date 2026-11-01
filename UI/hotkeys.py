"""Atajos de teclado: formato, validación y el gestor de atajos globales (pynput).

Formato global (pynput):  «<ctrl>+<alt>+o», «<f9>», «<shift>+<f2>».
Formato de la capa (Qt):  «T», «Ctrl+M», «F2», «Esc».
"""
import re

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence

MODIFIERS = ("ctrl", "alt", "shift", "cmd")
_MOD_ORDER = {m: i for i, m in enumerate(MODIFIERS)}
_NAMED = {"space", "enter", "esc", "tab", "backspace", "delete", "insert", "home", "end", "page_up",
          "page_down", "up", "down", "left", "right"}
_FKEY = re.compile(r"^f([1-9]|1\d|2[0-4])$")
_QT_NAMED = {Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Escape: "esc",
             Qt.Key_Tab: "tab", Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete", Qt.Key_Insert: "insert",
             Qt.Key_Home: "home", Qt.Key_End: "end", Qt.Key_PageUp: "page_up", Qt.Key_PageDown: "page_down",
             Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right"}
_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Mayús", "cmd": "Win", "page_up": "RePág",
           "page_down": "AvPág", "esc": "Esc", "enter": "Intro", "space": "Espacio", "backspace": "Retroceso",
           "delete": "Supr", "insert": "Ins", "home": "Inicio", "end": "Fin", "tab": "Tab",
           "up": "↑", "down": "↓", "left": "←", "right": "→"}


def _split(combo: str):
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    mods, keys = [], []
    for p in parts:
        name = p[1:-1] if p.startswith("<") and p.endswith(">") else p
        alias = {"control": "ctrl", "mayus": "shift", "win": "cmd", "meta": "cmd", "super": "cmd",
                 "option": "alt"}.get(name, name)
        (mods if alias in MODIFIERS else keys).append(alias)
    return mods, keys


def normalize_global(combo: str):
    """Atajo global en formato pynput, o None si no es válido (necesita un modificador o una tecla F)."""
    mods, keys = _split(combo or "")
    if len(keys) != 1 or len(set(mods)) != len(mods):
        return None
    key = keys[0]
    if not (len(key) == 1 and key.isalnum() or key in _NAMED or _FKEY.match(key)):
        return None
    if not mods and not _FKEY.match(key):
        return None
    mods.sort(key=_MOD_ORDER.get)
    return "+".join([f"<{m}>" for m in mods] + [key if len(key) == 1 else f"<{key}>"])


def global_label(combo: str) -> str:
    """«<ctrl>+<alt>+o» -> «Ctrl+Alt+O»."""
    mods, keys = _split(combo or "")
    out = [_LABELS.get(m, m.title()) for m in mods]
    for k in keys:
        out.append(_LABELS.get(k) or (k.upper() if len(k) <= 3 else k.title()))
    return "+".join(out)


def global_from_event(modifiers, key):
    """Atajo global (pynput) de un evento de teclado de Qt, o None si todavía es solo un modificador."""
    if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr, 0):
        return None
    mods = []
    if modifiers & Qt.ControlModifier:
        mods.append("ctrl")
    if modifiers & Qt.AltModifier:
        mods.append("alt")
    if modifiers & Qt.ShiftModifier:
        mods.append("shift")
    if modifiers & Qt.MetaModifier:
        mods.append("cmd")
    if key in _QT_NAMED:
        name = _QT_NAMED[key]
    elif Qt.Key_F1 <= key <= Qt.Key_F24:
        name = f"f{key - Qt.Key_F1 + 1}"
    elif 0x20 < key < 0x7F:
        name = chr(key).lower()
    else:
        return None
    return normalize_global("+".join(mods + [name]))


def overlay_key_from_event(modifiers, key):
    """Atajo de la capa (texto de Qt, «T», «Ctrl+M»), o None si es solo un modificador."""
    if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr, 0):
        return None
    mods = modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
    text = QKeySequence(int(mods) | int(key)).toString(QKeySequence.PortableText)
    return normalize_overlay(text)


def normalize_overlay(text: str):
    text = (text or "").strip()
    if not text:
        return None
    seq = QKeySequence(text, QKeySequence.PortableText)
    if seq.isEmpty() or seq.count() != 1:
        return None
    return seq.toString(QKeySequence.PortableText) or None


def overlay_matches(text: str, modifiers, key) -> bool:
    """¿El evento (modificadores, tecla) es el atajo guardado `text`?"""
    return overlay_key_from_event(modifiers, key) == normalize_overlay(text)


class HotkeyManager(QObject):
    """Atajos globales con pynput. `triggered(acción)` se emite al pulsarlos."""
    triggered = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._listener = None
        self._bindings = {}
        self.error = ""
        try:
            from pynput import keyboard  # noqa: F401
            self.available = True
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self.error = str(exc)

    @property
    def bindings(self) -> dict:
        return dict(self._bindings)

    def set_bindings(self, bindings: dict) -> bool:
        """Sustituye todos los atajos ({acción: «<ctrl>+<alt>+o»}). Devuelve False si no se pudo."""
        clean = {}
        for action, combo in bindings.items():
            norm = normalize_global(combo)
            if norm and norm not in clean.values():
                clean[action] = norm
        self._bindings = clean
        self._stop_listener()
        if not self.available or not clean:
            return self.available
        try:
            from pynput import keyboard
            table = {combo: (lambda a=action: self.triggered.emit(a)) for action, combo in clean.items()}
            self._listener = keyboard.GlobalHotKeys(table)
            self._listener.start()
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self._listener = None
            return False

    def _stop_listener(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None

    def stop(self):
        self._stop_listener()
