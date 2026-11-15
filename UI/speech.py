"""Voz: lee en voz alta una palabra o una frase del juego con las voces que ya trae Windows (sin instalar nada más).

- Síntesis: un script de PowerShell guarda un .wav. Primero prueba las voces del paquete de idioma de Windows
  (Configuración ▸ Hora e idioma ▸ Voz; las de «Microsoft Haruka», «Ayumi», «Ichiro»…) y, si no hay,
  las clásicas de SAPI (System.Speech). Si no hay ninguna voz del idioma, avisa cómo instalarla.
- Caché: cada audio se guarda en `data/audio/` (clave: idioma + texto), así la segunda vez suena al instante y
  el mismo archivo servirá más adelante para las cartas Anki.
- Reproducción: `winsound` (viene con Python en Windows). Fuera de Windows no hay voz y se avisa.

El botón 🔊 (`SpeakButton`) está dibujado a mano para que se vea igual con cualquier fuente y tema.
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import weakref
from pathlib import Path

from PyQt5.QtCore import QObject, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QPushButton

CACHE_DIR = Path(__file__).resolve().parent / "data" / "audio"
MAX_CHARS = 600                      # una frase más larga se corta (la voz tardaría demasiado)
MAX_CACHE_MB = 200
LANG_NAMES = {"ja": "japonés", "en": "inglés", "fr": "francés"}
EXIT_NO_VOICE = 3

# Script de PowerShell (solo ASCII: así no depende de la codificación del archivo). Recibe el texto en un archivo UTF-8.
POWERSHELL = r"""
param([string]$TextFile, [string]$Out, [string]$Lang)
$ErrorActionPreference = 'Stop'
$text = [IO.File]::ReadAllText($TextFile, [Text.Encoding]::UTF8)
# 1) Voces de Windows (paquete de idioma / "Voz" en Configuracion)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [void][Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime]
    [void][Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType=WindowsRuntime]
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
    function Await($op, $type) { $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); [void]$task.Wait(-1); $task.Result }
    $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object { $_.Language -like "$Lang*" } | Select-Object -First 1
    if ($voice) {
        $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
        $synth.Voice = $voice
        $stream = Await ($synth.SynthesizeTextToStreamAsync($text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
        $size = [uint32]$stream.Size
        $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
        [void](Await ($reader.LoadAsync($size)) ([uint32]))
        $bytes = New-Object byte[] $size
        $reader.ReadBytes($bytes)
        [IO.File]::WriteAllBytes($Out, $bytes)
        exit 0
    }
} catch { }
# 2) Voces clasicas (SAPI)
try {
    Add-Type -AssemblyName System.Speech
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like "$Lang*" } | Select-Object -First 1
    if (-not $v) { exit 3 }
    $s.SelectVoice($v.VoiceInfo.Name)
    $s.SetOutputToWaveFile($Out)
    $s.Speak($text)
    $s.Dispose()
    exit 0
} catch { exit 4 }
"""


def is_windows() -> bool:
    return sys.platform.startswith("win")


def clean_text(text: str, lang: str = "") -> str:
    """Texto listo para la voz: sin saltos de línea ni espacios repetidos (en japonés, sin espacios) y con tope de largo."""
    text = re.sub(r"\s+", "" if lang == "ja" else " ", str(text or "")).strip()
    return text[:MAX_CHARS]


def cache_key(text: str, lang: str) -> str:
    return hashlib.sha1(f"{lang}\n{text}".encode("utf-8")).hexdigest()[:20]


def no_voice_message(lang: str) -> str:
    name = LANG_NAMES.get(lang, lang)
    return (f"No hay una voz de {name} en Windows. Agrégala en Configuración ▸ Hora e idioma ▸ Idioma y región "
            f"({name}) ▸ opciones del idioma ▸ Voz.")


def windows_synth(text: str, lang: str, out_path: Path):
    """Sintetiza `text` en `out_path` (.wav) con la voz de Windows. Devuelve None si salió bien o un código/mensaje de error."""
    work = Path(tempfile.mkdtemp(prefix="ocrvoz_"))
    try:
        script, text_file, tmp = work / "voz.ps1", work / "texto.txt", work / "salida.wav"
        script.write_text(POWERSHELL, encoding="ascii")
        text_file.write_text(text, encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-TextFile", str(text_file), "-Out", str(tmp), "-Lang", lang],
            capture_output=True, timeout=40, creationflags=flags)
        if proc.returncode == EXIT_NO_VOICE:
            return "no_voice"
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 100:
            return "No se pudo generar la voz (PowerShell terminó con error)."
        out_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, out_path)
        return None
    except FileNotFoundError:
        return "No se encontró PowerShell para generar la voz."
    except subprocess.TimeoutExpired:
        return "La voz de Windows tardó demasiado."
    except Exception as exc:  # noqa: BLE001
        return f"No se pudo generar la voz: {exc}"
    finally:
        for f in work.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass


def winsound_play(path: Path):
    import winsound
    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)


def winsound_stop():
    import winsound
    winsound.PlaySound(None, winsound.SND_PURGE)


def prune_cache(folder: Path, max_mb: int = MAX_CACHE_MB):
    """Borra los audios más viejos si la caché pasa de `max_mb`."""
    try:
        files = sorted((f for f in folder.glob("*.wav")), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        for f in files:
            if total <= max_mb * 1024 * 1024:
                break
            total -= f.stat().st_size
            f.unlink()
    except OSError:
        pass


class Speaker(QObject):
    """Lee textos en voz alta. `synth(text, lang, path)` y `play(path)` / `stop_play()` se pueden sustituir (pruebas)."""
    failed = pyqtSignal(str)                 # mensaje para mostrar al usuario
    loading_changed = pyqtSignal(bool)       # True mientras se genera un audio nuevo; False al terminar (suene o falle)
    _finished = pyqtSignal(str, str, object)    # clave, ruta, error (None = bien)

    def __init__(self, cache_dir: Path = CACHE_DIR, synth=None, play=None, stop_play=None, parent=None):
        super().__init__(parent)
        self.cache_dir = Path(cache_dir)
        windows = is_windows()
        self._synth = synth or (windows_synth if windows else None)
        self._play = play or (winsound_play if windows else None)
        self._stop = stop_play or (winsound_stop if windows else None)
        self._want = None                    # clave del último audio pedido (los anteriores que lleguen tarde no suenan)
        self._pending = set()                # claves que se están generando
        self._langs = {}
        self._finished.connect(self._on_finished)
        prune_cache(self.cache_dir)

    @property
    def available(self) -> bool:
        return self._synth is not None and self._play is not None

    def path_for(self, text: str, lang: str) -> Path:
        return self.cache_dir / f"{lang}_{cache_key(text, lang)}.wav"

    def is_cached(self, text: str, lang: str) -> bool:
        text = clean_text(text, lang)
        p = self.path_for(text, lang)
        return bool(text) and p.exists() and p.stat().st_size > 100

    def speak(self, text: str, lang: str) -> bool:
        """Pide leer `text` (en el idioma `lang`). Devuelve False si no hay nada que leer o no hay voz en este sistema."""
        text = clean_text(text, lang)
        if not text:
            return False
        if not self.available:
            self.failed.emit("La voz solo está disponible en Windows.")
            self.loading_changed.emit(False)
            return False
        self.stop()
        key = f"{lang}:{cache_key(text, lang)}"
        self._want = key
        path = self.path_for(text, lang)
        if path.exists() and path.stat().st_size > 100:
            self._start(path)
            self.loading_changed.emit(False)
            return True
        if key in self._pending:
            self.loading_changed.emit(True)
            return True
        self._pending.add(key)
        self._langs[key] = lang
        self.loading_changed.emit(True)
        threading.Thread(target=self._work, args=(key, text, lang, path), daemon=True).start()
        return True

    def stop(self):
        self._want = None
        if self._stop is not None:
            try:
                self._stop()
            except Exception:  # noqa: BLE001
                pass

    # --- internos ---
    def _work(self, key, text, lang, path):
        try:
            err = self._synth(text, lang, path)
        except Exception as exc:  # noqa: BLE001
            err = f"No se pudo generar la voz: {exc}"
        self._finished.emit(key, str(path), err)

    def _on_finished(self, key, path, err):
        self._pending.discard(key)
        lang = self._langs.pop(key, "")
        self.loading_changed.emit(False)
        if err:
            if key == self._want:             # solo avisa del último pedido
                self._want = None
                self.failed.emit(no_voice_message(lang) if err == "no_voice" else str(err))
            return
        if key == self._want:
            self._start(Path(path))

    def _start(self, path: Path):
        try:
            self._play(path)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"No se pudo reproducir el audio: {exc}")


class SpeakButton(QPushButton):
    """Botón 🔊 dibujado a mano (un altavoz con ondas). Se ilumina al pasar el mouse y se pone azul mientras se genera la voz."""
    _all = weakref.WeakSet()

    def __init__(self, tip="Escuchar", size=(26, 26)):
        super().__init__()
        self.setToolTip(tip)
        self.setFixedSize(*size)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._loading = False
        self._hover = False
        self.setAttribute(Qt.WA_Hover, True)
        SpeakButton._all.add(self)
        self.clicked.connect(self._mark)

    def _mark(self):
        self._loading = True
        self.update()

    @classmethod
    def clear_loading(cls):
        for b in list(cls._all):
            try:
                b._loading = False
                b.update()
            except RuntimeError:              # el widget ya se destruyó
                pass

    def sizeHint(self):
        return QSize(26, 26)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 34))
            p.drawRoundedRect(QRectF(0, 0, w, h), 6, 6)
        color = QColor("#38b6dc") if self._loading else QColor("#ffffff" if self._hover else "#b8c2d2")
        s = min(w, h) / 26.0
        cx, cy = w / 2.0, h / 2.0
        body = QPainterPath()                  # el altavoz: cuadradito + cono
        body.moveTo(cx - 7 * s, cy - 2.6 * s)
        body.lineTo(cx - 3.6 * s, cy - 2.6 * s)
        body.lineTo(cx + 0.6 * s, cy - 6.2 * s)
        body.lineTo(cx + 0.6 * s, cy + 6.2 * s)
        body.lineTo(cx - 3.6 * s, cy + 2.6 * s)
        body.lineTo(cx - 7 * s, cy + 2.6 * s)
        body.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(body)
        pen = QPen(color, 1.6 * s)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for r, span in ((3.6, 70), (6.8, 70)):          # las ondas
            rect = QRectF(cx + 0.6 * s - r * s, cy - r * s, 2 * r * s, 2 * r * s)
            p.drawArc(rect, -span * 16, span * 2 * 16)
        p.end()
