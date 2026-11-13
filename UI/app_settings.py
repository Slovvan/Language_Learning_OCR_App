"""Ajustes de la app: valores por defecto, atajos globales y lectura/escritura con QSettings."""
from UI.hotkeys import global_label, normalize_global

# acción -> (atajo por defecto, nombre que ve el usuario)
ACTIONS = {
    "capture": ("<ctrl>+<alt>+o", "Capturar región / elegir la región fija"),
    "read": ("<ctrl>+<alt>+r", "Leer la región (modo en vivo)"),
    "auto": ("<ctrl>+<alt>+a", "Lectura automática: activar / desactivar"),
    "freeze": ("<ctrl>+<alt>+f", "Congelar / descongelar la pantalla"),
    "text": ("<ctrl>+<alt>+h", "Quitar / poner el texto sobre el original"),
    "panel": ("<ctrl>+<alt>+t", "Abrir / cerrar el panel de traducción"),
    "merge": ("<ctrl>+<alt>+m", "Unir / separar líneas"),
    "edit": ("<ctrl>+<alt>+e", "Mover / estirar la región"),
}

# clave -> (por defecto, mínimo, máximo)
NUMBERS = {
    "live_sensitivity": (5, 1, 10),         # sensibilidad al cambio del texto (lectura automática)
    "live_stable_ms": (450, 150, 3000),     # cuánto debe quedarse quieto el texto antes de leerlo
    "live_min_gap_ms": (900, 300, 10000),   # separación mínima entre dos lecturas automáticas
    "live_history_max": (30, 5, 200),       # lecturas que guarda el historial del panel
    "live_panel_hide_s": (6, 2, 60),        # segundos que dura el panel temporal
    "live_panel_font": (16, 11, 32),        # tamaño de letra del panel (px)
    "live_text_opacity": (100, 30, 100),    # opacidad (%) del fondo del texto dibujado sobre la región
    "popup_scale": (100, 70, 200),          # tamaño (%) de la letra de la ventanita del diccionario
    "frozen_dim": (47, 10, 90),             # oscurecimiento (%) de lo que queda fuera de la región en la captura congelada
}


PANEL_MODES = ("full", "minimal", "tab")


def panel_mode(settings) -> str:
    """Aspecto del panel de traducción: «full» (completo), «minimal» o «tab» (viñeta). Lo guardado antes de que existieran
    los tres aspectos era solo «viñeta sí / no» (`live_panel_tab`): se respeta."""
    mode = str(settings.value("live_panel_mode", "") or "")
    if mode in PANEL_MODES:
        return mode
    return "tab" if settings.value("live_panel_tab", False) in (True, "true", "1", 1) else "full"


def get_number(settings, key: str) -> int:
    default, lo, hi = NUMBERS[key]
    try:
        value = int(float(settings.value(key, default)))
    except (TypeError, ValueError, OverflowError):      # «abc», «nan», «inf»...
        value = default
    return max(lo, min(hi, value))


def get_ocr_bar(settings) -> str:
    """Cómo se muestra la barra de modos OCR: «hover» (viñeta), «always» o «hidden»."""
    value = str(settings.value("ocr_bar", "hover"))
    return value if value in ("hover", "always", "hidden") else "hover"


def default_bindings() -> dict:
    return {a: combo for a, (combo, _name) in ACTIONS.items()}


def load_bindings(settings) -> dict:
    """{acción: atajo}; lo guardado y válido pisa el valor por defecto. Un valor vacío deja la acción sin atajo."""
    out = {}
    for action, (default, _name) in ACTIONS.items():
        saved = settings.value(f"hk/{action}", None)
        if saved is None:
            out[action] = default
        else:
            out[action] = normalize_global(str(saved)) or ""
    return out


def hotkey_label(settings, action: str) -> str:
    """«Ctrl+Alt+O» (o «sin atajo»)."""
    combo = load_bindings(settings).get(action, "")
    return global_label(combo) if combo else "sin atajo"


# --- teclas con el foco (en la captura congelada o en la región / panel del modo en vivo): una tecla, con o sin Ctrl/Alt/Mayús ---
FROZEN_KEYS = {          # acción -> (tecla por defecto, nombre)
    "translation": ("T", "Mostrar / ocultar la traducción"),
    "original": ("O", "Mostrar / ocultar el texto original sobre la captura"),
    "merge": ("M", "Unir / separar líneas"),
    "close": ("Esc", "Cerrar la captura"),
    "mode_normal": ("1", "OCR normal (japonés)"),
    "mode_kana": ("2", "OCR solo kana (japonés)"),
    "mode_vertical": ("3", "OCR vertical (japonés)"),
}
LIVE_KEYS = {
    "read": ("R", "Leer la región ahora"),
    "panel": ("T", "Abrir / cerrar el panel de traducción"),
    "text": ("O", "Quitar / poner el texto sobre el original"),
    "merge": ("M", "Unir / separar líneas"),
    "edit": ("E", "Mover / estirar la región"),
    "auto": ("A", "Lectura automática: activar / desactivar"),
    "freeze": ("F", "Congelar / descongelar la pantalla"),
    "mode_normal": ("1", "OCR normal (japonés)"),
    "mode_kana": ("2", "OCR solo kana (japonés)"),
    "mode_vertical": ("3", "OCR vertical (japonés)"),
    "stop": ("Esc", "Detener el modo en vivo"),
}
KEY_SCOPES = {"frozen": FROZEN_KEYS, "live": LIVE_KEYS}


def default_keys(scope: str) -> dict:
    return {a: key for a, (key, _n) in KEY_SCOPES[scope].items()}


def load_keys(settings, scope: str) -> dict:
    """{acción: tecla de Qt como texto («T», «Ctrl+M», «Esc»)}; vacío = sin tecla."""
    from UI.hotkeys import normalize_overlay
    out = {}
    for action, (default, _n) in KEY_SCOPES[scope].items():
        saved = settings.value(f"key/{scope}/{action}", None)
        out[action] = default if saved is None else (normalize_overlay(str(saved)) or "")
    return out


def key_action(keys: dict, modifiers, key):
    """Acción cuya tecla es (modificadores, tecla), o None."""
    from UI.hotkeys import overlay_matches
    for action, text in keys.items():
        if text and overlay_matches(text, modifiers, key):
            return action
    return None
