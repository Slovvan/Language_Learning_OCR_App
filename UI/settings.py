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
    ("live_toggle", "Modo en vivo: activar / detener", "Empieza o termina de vigilar la región fija.",
     "<ctrl>+<alt>+l"),
    ("read_now", "Modo en vivo: leer ahora", "Lee la región fija en este momento (el botón de lectura manual).",
     "<ctrl>+<alt>+r"),
    ("pause", "Modo en vivo: pausar / reanudar", "Deja de leer sin cerrar la región (útil en cinemáticas).",
     "<ctrl>+<alt>+p"),
    ("panel", "Modo en vivo: mostrar / ocultar el panel", "Esconde o devuelve el panel de texto y traducción.",
     "<ctrl>+<alt>+h"),
    ("freeze", "Modo en vivo: congelar la pantalla y leer", "Congela la pantalla un momento con el texto encima (como el modo congelar), "
     "sin tener que elegir la región otra vez.", "<ctrl>+<alt>+f"),
    ("edit_region", "Modo en vivo: mover / cambiar de tamaño la región", "Activa o termina la edición de la región fija "
     "(arrastra el marco para moverla, los bordes para cambiar su tamaño).", "<ctrl>+<alt>+m"),
    ("interact", "Modo en vivo: consultar palabras con el mouse", "Activa o desactiva el mouse sobre el texto de la región "
     "(significado al dejarlo quieto, clic para guardar). Mientras está activo, los clics no llegan al juego.", "<ctrl>+<alt>+i"),
    ("layer", "Modo en vivo: mostrar / ocultar el texto sobre la región", "Alterna el texto que se dibuja encima del original.",
     "<ctrl>+<alt>+k"),
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
    "capture.mode": ("frozen", "live"),              # congelar la pantalla / región fija en vivo
    "live.trigger": ("auto", "button", "click"),     # cuándo se lee en vivo
    "live.panel_side": ("auto", "below", "above", "left", "right"),
    "live.show_frame": ("always", "hidden"),         # marco alrededor de la región fija
    "live.layer_content": ("translation", "original"),   # qué se dibuja encima de la región
    "live.panel_show": ("always", "hidden"),         # panel de texto/traducción abierto u oculto
    "live.panel_mode": ("log", "last"),              # historial con las últimas lecturas / solo la última
}

# Rangos (mínimo, máximo) de los ajustes numéricos del modo en vivo.
RANGES = {
    "live.sensitivity": (1, 10),
    "live.interval_ms": (100, 2000),
    "live.stable_ms": (100, 3000),
    "live.min_gap_ms": (0, 10000),
    "live.panel_hide_s": (0, 120),
    "live.panel_font": (9, 40),
    "live.panel_opacity": (30, 100),
    "live.log_max": (3, 200),
    "live.panel_w": (0, 4000),
    "live.panel_h": (0, 4000),
}

DEFAULTS = {f"hotkey.{k}": d for k, _n, _h, d in GLOBAL_ACTIONS}
DEFAULTS.update({f"overlay.key.{k}": d for k, _n, d in OVERLAY_ACTIONS})
DEFAULTS.update({
    "ui.toolbar": "hover",
    "capture.mode": "frozen",
    "live.trigger": "auto",
    "live.sensitivity": 5,          # 1 = poco sensible, 10 = muy sensible
    "live.interval_ms": 300,        # cada cuánto se mira la región
    "live.stable_ms": 450,          # tiempo quieta antes de leer (el texto que se escribe letra a letra no se lee a medias)
    "live.min_gap_ms": 900,         # mínimo entre dos lecturas
    "live.panel_side": "auto",
    "live.panel_hide_s": 0,         # 0 = el panel no se oculta solo
    "live.panel_font": 15,
    "live.panel_opacity": 92,
    "live.show_frame": "always",
    "live.layer": True,             # dibujar el texto encima de la región (sin congelar)
    "live.layer_content": "original",   # como la captura congelada: el texto reconocido sobre el original
    "live.panel_show": "always",
    "live.panel_mode": "last",          # solo la última lectura
    "live.panel_original": False,       # el panel muestra solo la traducción
    "live.panel_w": 0,                  # tamaño del panel que dejó el usuario (0 = automático)
    "live.panel_h": 0,
    "live.log_max": 30,             # lecturas que guarda el panel en modo historial
    "live.read_freezes": False,     # «Leer ahora» congela la pantalla en vez de leer en vivo
    "live.profiles": "{}",          # JSON: {nombre: {region, idioma, modo, unir}}
})


def _coerce(key: str, value, default):
    """`value` convertido al tipo de `default` (QSettings devuelve texto en algunas plataformas)."""
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "si", "sí")
        return bool(value)
    if isinstance(default, int):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        if key in RANGES:
            lo, hi = RANGES[key]
            number = max(lo, min(hi, number))
        return number
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
