"""Ajustes de la aplicación (se guardan en QSettings) con sus valores por defecto.

    s = AppSettings(QSettings("OCRTranslator", "fase6"))
    s.get("hotkey.capture")           # "<ctrl>+<alt>+o"
    s.set("ui.toolbar", "hover")      # emite s.changed("ui.toolbar")

Cada valor se valida al leerlo: si el guardado no es válido se usa el de por defecto.
Los atajos globales usan el formato de pynput («<ctrl>+<alt>+o»); los de la capa, el de Qt («T», «Ctrl+M»).
"""
from PyQt5.QtCore import QObject, pyqtSignal

# --- acciones con atajo global (funcionan aunque el juego tenga el foco) ---
# (clave, nombre, ayuda, atajo por defecto)
GLOBAL_ACTIONS = [
    ("capture", "Capturar región", "Congela la pantalla y eliges la región (o, en modo en vivo, la región fija).",
     "<ctrl>+<alt>+o"),
]
# --- teclas dentro de la capa de captura ---
OVERLAY_ACTIONS = [
    ("translation", "Mostrar / ocultar la traducción", "T"),
    ("layer", "Mostrar / ocultar el texto sobre el original", "O"),
    ("merge", "Unir / separar las líneas partidas", "M"),
    ("mode_normal", "OCR normal (japonés)", "1"),
    ("mode_kana", "OCR solo kana (japonés)", "2"),
    ("mode_vertical", "OCR texto vertical (japonés)", "3"),
    ("close", "Cerrar la captura", "Esc"),
]

CHOICES = {
    "ui.toolbar": ("always", "hover", "hidden"),     # barra de modos de OCR en la capa
}

DEFAULTS = {f"hotkey.{k}": d for k, _n, _h, d in GLOBAL_ACTIONS}
DEFAULTS.update({f"overlay.key.{k}": d for k, _n, d in OVERLAY_ACTIONS})
DEFAULTS.update({
    "ui.toolbar": "hover",
})


def _coerce(key: str, value, default):
    """`value` convertido al tipo de `default` (QSettings devuelve texto en algunas plataformas)."""
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "si", "sí")
        return bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    value = str(value)
    if key in CHOICES and value not in CHOICES[key]:
        return default
    return value


class AppSettings(QObject):
    changed = pyqtSignal(str)                 # clave modificada

    def __init__(self, qsettings):
        super().__init__()
        self._q = qsettings

    def get(self, key: str):
        default = DEFAULTS[key]
        value = self._q.value("app/" + key, default)
        if value is None or value == "":
            return default
        return _coerce(key, value, default)

    def set(self, key: str, value):
        if key not in DEFAULTS:
            raise KeyError(key)
        value = _coerce(key, value, DEFAULTS[key])
        if value == self.get(key) and self._q.contains("app/" + key):
            return
        self._q.setValue("app/" + key, value)
        self.changed.emit(key)

    def reset(self, key: str):
        self._q.remove("app/" + key)
        self.changed.emit(key)

    def reset_all(self, prefix: str = ""):
        for key in DEFAULTS:
            if key.startswith(prefix):
                self.reset(key)

    def hotkeys(self) -> dict:
        """{acción: atajo global} de todas las acciones."""
        return {k: self.get(f"hotkey.{k}") for k, *_ in GLOBAL_ACTIONS}

    def overlay_keys(self) -> dict:
        return {k: self.get(f"overlay.key.{k}") for k, *_ in OVERLAY_ACTIONS}
