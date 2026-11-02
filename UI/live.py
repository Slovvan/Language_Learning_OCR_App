"""Modo en vivo: vigila una región fija de la pantalla y lee el texto cuando cambia.

    ChangeDetector   decide CUÁNDO leer (sin Qt; se prueba con fotogramas inventados).
    LiveController   cada `interval_ms` captura la región, se la pasa al detector y, si toca leer,
                     emite `capture(imagen)`. No congela nada: el juego sigue funcionando.
    InputTrigger     (opcional, pynput) avisa de clics/teclas del juego para el disparador «al hacer clic».

Lectura automática: cuando la región cambia y se queda quieta `stable_ms` (así el texto que aparece letra a letra
no se lee a medias), separando las lecturas al menos `min_gap_ms`. Las lecturas manuales (botón / atajo) siguen igual.
"""
import re
import time

import numpy as np
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

SIG_W, SIG_H = 96, 32          # tamaño de la firma (gris reducida) con la que se compara
CELL_DELTA = 16                # diferencia de gris a partir de la cual una celda cuenta como «cambiada»
MOVE_FRACTION = 0.0006         # cambio mínimo entre dos fotogramas seguidos para decir «se está moviendo» (~2 celdas)
MAX_WAIT_MS = 4000             # si la región no deja de moverse (fondo animado), se lee igualmente tras este tiempo


def signature(frame: np.ndarray) -> np.ndarray:
    """Imagen gris pequeña (SIG_H x SIG_W) para comparar fotogramas barato."""
    import cv2
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (SIG_W, SIG_H), interpolation=cv2.INTER_AREA).astype(np.int16)


def changed_fraction(a: np.ndarray, b: np.ndarray) -> float:
    return float((np.abs(a - b) > CELL_DELTA).mean())


def threshold_for(sensitivity: int) -> float:
    """Fracción de celdas cambiadas que cuenta como «hay texto nuevo» (1 = exige mucho, 10 = poco)."""
    s = max(1, min(10, int(sensitivity)))
    return 0.0015 * (11 - s)


def normalize_text(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower())


class ChangeDetector:
    """Decide cuándo leer. Se alimenta con `feed(firma, t_ms)`; devuelve True si hay que leer ya."""

    def __init__(self, sensitivity=5, stable_ms=450, min_gap_ms=900, max_wait_ms=MAX_WAIT_MS):
        self.threshold = threshold_for(sensitivity)
        self.stable_ms = stable_ms
        self.min_gap_ms = min_gap_ms
        self.max_wait_ms = max_wait_ms
        self.reset()

    def reset(self):
        self.ref = None                 # lo último que se leyó
        self.last = None                # el fotograma anterior
        self.stable_since = None
        self.moving_since = None
        self.last_read = None
        self.state = "idle"             # idle (igual que lo leído) / changing (se mueve) / pending (quieto pero sin leer)

    def mark_read(self, sig, t_ms):
        self.ref = sig
        self.last_read = t_ms
        self.stable_since = self.moving_since = None
        self.state = "idle"

    def feed(self, sig, t_ms, allowed=True) -> bool:
        """`allowed` = False mientras hay pausa, el worker está ocupado o (modo clic) no hay clic reciente."""
        first = self.ref is None
        moving = self.last is not None and changed_fraction(sig, self.last) > MOVE_FRACTION
        self.last = sig
        if moving:
            self.stable_since = None
            if self.moving_since is None:
                self.moving_since = t_ms
        elif self.stable_since is None:
            self.stable_since = t_ms
            self.moving_since = None
        differs = first or changed_fraction(sig, self.ref) > self.threshold
        if not differs:
            self.state = "idle"
            return False
        self.state = "changing" if moving else "pending"
        if not allowed:
            return False
        if self.last_read is not None and t_ms - self.last_read < self.min_gap_ms:
            return False
        quiet = self.stable_since is not None and t_ms - self.stable_since >= self.stable_ms
        stuck = self.moving_since is not None and t_ms - self.moving_since >= self.max_wait_ms
        if quiet or stuck:
            self.mark_read(sig, t_ms)
            return True
        return False


class LiveController(QObject):
    """Cada `interval_ms` captura la región y, cuando el detector dice que toca leer, emite `capture(imagen)`.
    No lee mientras hay una lectura en curso, en pausa o con la vigilancia detenida."""
    capture = pyqtSignal(object)        # imagen BGR que hay que leer
    state_changed = pyqtSignal(str)     # "off" / "watching" / "changing" / "paused" / "reading"

    def __init__(self, grab_fn, now_fn=None, parent=None):
        super().__init__(parent)
        self._grab = grab_fn            # grab_fn(caja) -> imagen BGR
        self._now = now_fn or (lambda: time.monotonic() * 1000.0)
        self.box = None
        self.paused = False
        self.busy = False
        self.detector = ChangeDetector()
        self.last_key = ""              # texto (normalizado) de la última lectura mostrada
        self.hold_fn = None             # hold_fn() -> True: no se lee ahora (p. ej. hay una ventanita encima de la región)
        self._state = "off"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        self._timer.setInterval(300)

    def configure(self, sensitivity=5, interval_ms=300, stable_ms=450, min_gap_ms=900):
        det = self.detector
        det.threshold = threshold_for(sensitivity)
        det.stable_ms, det.min_gap_ms = stable_ms, min_gap_ms
        self._timer.setInterval(max(50, int(interval_ms)))

    @property
    def active(self) -> bool:
        return self.box is not None and self._timer.isActive()

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    # ------------------------------------------------------------------ control
    def start(self, box):
        """Empieza a vigilar `box` = (x0, y0, x1, y1) en píxeles reales del monitor."""
        self.box = tuple(int(v) for v in box)
        self.detector.reset()
        self.paused = self.busy = False
        self._timer.start()
        self._set_state("watching")

    def stop(self):
        self._timer.stop()
        self._set_state("off")

    def set_box(self, box):
        self.box = tuple(int(v) for v in box)
        self.detector.reset()

    def set_paused(self, paused: bool):
        self.paused = bool(paused)
        if self._timer.isActive():
            self._set_state("paused" if self.paused else "watching")

    def set_busy(self, busy: bool):
        self.busy = bool(busy)
        if self._timer.isActive() and not self.paused:
            self._set_state("reading" if busy else "watching")

    def mark_read_now(self):
        """Hubo una lectura manual: lo que hay en pantalla cuenta como ya leído."""
        if self.box is None:
            return
        try:
            self.detector.mark_read(signature(self._grab(self.box)), self._now())
        except Exception:  # noqa: BLE001
            self.detector.reset()

    def accept_text(self, text: str) -> bool:
        """¿El texto leído es distinto del anterior? (se guarda como el último). Evita repetir en el registro
        la misma frase cuando cambió algo que no era texto (un fondo, un parpadeo...)."""
        key = normalize_text(text)
        if not key or key == self.last_key:
            return False
        self.last_key = key
        return True

    # ------------------------------------------------------------------ bucle
    def tick(self):
        if self.box is None:
            return
        try:
            frame = self._grab(self.box)
        except Exception:  # noqa: BLE001   (la ventana se movió, pantalla bloqueada...: se reintenta)
            return
        sig = signature(frame)
        if self.paused or self.busy or (self.hold_fn is not None and self.hold_fn()):
            self.detector.last = sig             # se sigue el movimiento, pero no se lee
            return
        if self.detector.feed(sig, self._now(), True):
            self.capture.emit(frame)
        else:
            self._set_state("changing" if self.detector.state == "changing" else "watching")
