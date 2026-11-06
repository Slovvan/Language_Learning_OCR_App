"""Opciones de sí / no del modo en vivo. Una sola fuente de verdad (QSettings) que comparten la ventana de Configuración
y la tuerca del panel: al cambiar una en un sitio, la otra se actualiza."""
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QCheckBox

# clave, texto, ayuda, valor por defecto, grupo
OPTIONS = (
    ("live_auto", "Lectura automática",
     "Lee solo cuando el texto de la región cambia y se queda quieto. Desmarcada, solo se lee con «Leer».", False, "read"),
    ("live_freeze_read", "Congelar al leer",
     "Tras «Leer», la pantalla se queda congelada con el texto encima. Ojo: el juego sigue por detrás "
     "(para detenerlo usa la tecla de pausa del juego).", False, "read"),
    ("live_freeze_auto", "Congelar en automático",
     "Cada frase detectada por la lectura automática congela la pantalla.", False, "read"),
    ("live_pause_on", "Pausar el juego al congelar",
     "Al congelar la pantalla se envía la tecla de pausa del juego (se configura en Configuración) y se vuelve a enviar al "
     "descongelar. Necesita la tecla de pausa del juego bien configurada.", False, "read"),
    ("live_show_original", "Original en el panel",
     "El panel muestra, además de la traducción, el texto reconocido.", False, "panel"),
    ("live_history", "Historial",
     "El panel acumula las últimas lecturas (la más nueva abajo), cada una con ✕ para borrarla.", False, "panel"),
    ("live_save_log", "Guardar en el registro",
     "Cada lectura se guarda en «Registro de frases». Desmarcada, solo vive en el panel.", True, "panel"),
    ("live_over", "Traducción sobre la región",
     "Sobre la región se dibuja la traducción en lugar del texto reconocido (no se pueden guardar palabras con el mouse).", False, "panel"),
    ("live_panel_temp", "Panel temporal",
     "El panel aparece al leer y se esconde solo unos segundos después (si el mouse no está encima).", False, "panel"),
)
KEYS = tuple(o[0] for o in OPTIONS)
DEFAULTS = {o[0]: o[3] for o in OPTIONS}
_INFO = {o[0]: o for o in OPTIONS}


def _truthy(value) -> bool:
    return value in (True, "true", "1", 1)


class LiveOptions(QObject):
    changed = pyqtSignal(str, bool)          # clave, valor nuevo (solo cuando de verdad cambia)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def get(self, key: str) -> bool:
        return _truthy(self.settings.value(key, DEFAULTS[key]))

    def set(self, key: str, value: bool):
        value = bool(value)
        if self.get(key) == value and self.settings.value(key) is not None:
            return
        self.settings.setValue(key, value)
        self.changed.emit(key, value)

    def snapshot(self) -> dict:
        return {k: self.get(k) for k in KEYS}

    def apply(self, values: dict):
        for key in KEYS:
            if key in values:
                self.set(key, bool(values[key]))


def checkbox(model: LiveOptions, key: str, parent=None) -> QCheckBox:
    """Casilla ligada a la opción: sigue a las demás casillas de la misma opción."""
    _k, text, tip, _d, _g = _INFO[key]
    box = QCheckBox(text, parent)
    box.setToolTip(tip)
    box.setChecked(model.get(key))
    box.toggled.connect(lambda on, k=key: model.set(k, on))

    def follow(changed_key, value, b=box, k=key):
        if changed_key == k and b.isChecked() != value:
            b.blockSignals(True)
            b.setChecked(value)
            b.blockSignals(False)
    model.changed.connect(follow)
    return box
