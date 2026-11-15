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
import json
import os
import re
import shutil
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

# Script de PowerShell (solo ASCII: así no depende de la codificación del archivo). Recibe un lote en un JSON UTF-8
# ([{"i":0,"text":"...","out":"ruta.wav"}, ...]) y sintetiza todos con UNA sola voz abierta (arrancar PowerShell es lo lento).
# Imprime "OK i" o "ERR i" por elemento. Código de salida 3 = no hay voz de ese idioma.
POWERSHELL = r"""
param([string]$Manifest, [string]$Lang)
$ErrorActionPreference = 'Stop'
$items = @([IO.File]::ReadAllText($Manifest, [Text.Encoding]::UTF8) | ConvertFrom-Json)
$mode = ''
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
        $mode = 'winrt'
    }
} catch { $mode = '' }
# 2) Voces clasicas (SAPI)
if ($mode -eq '') {
    try {
        Add-Type -AssemblyName System.Speech
        $sapi = New-Object System.Speech.Synthesis.SpeechSynthesizer
        $v = $sapi.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like "$Lang*" } | Select-Object -First 1
        if (-not $v) { exit 3 }
        $sapi.SelectVoice($v.VoiceInfo.Name)
        $mode = 'sapi'
    } catch { exit 4 }
}
foreach ($it in $items) {
    try {
        if ($mode -eq 'winrt') {
            $stream = Await ($synth.SynthesizeTextToStreamAsync([string]$it.text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
            $size = [uint32]$stream.Size
            $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
            [void](Await ($reader.LoadAsync($size)) ([uint32]))
            $bytes = New-Object byte[] $size
            $reader.ReadBytes($bytes)
            [IO.File]::WriteAllBytes([string]$it.out, $bytes)
            $reader.Dispose(); $stream.Dispose()
        } else {
            $sapi.SetOutputToWaveFile([string]$it.out)
            $sapi.Speak([string]$it.text)
            $sapi.SetOutputToNull()
        }
        Write-Output ("OK " + $it.i)
    } catch { Write-Output ("ERR " + $it.i) }
}
exit 0
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


def windows_synth_batch(jobs, lang: str):
    """Sintetiza varios textos con UNA llamada a PowerShell. `jobs`: [(texto, ruta .wav)]. Devuelve una lista de errores
    (None = bien, «no_voice» o un mensaje) con el mismo orden que `jobs`."""
    jobs = list(jobs)
    if not jobs:
        return []
    work = Path(tempfile.mkdtemp(prefix="ocrvoz_"))
    try:
        script, manifest = work / "voz.ps1", work / "lote.json"
        script.write_text(POWERSHELL, encoding="ascii")
        tmp_paths = [work / f"{i}.wav" for i in range(len(jobs))]
        manifest.write_text(json.dumps([{"i": i, "text": t, "out": str(tmp_paths[i])} for i, (t, _p) in enumerate(jobs)],
                                       ensure_ascii=False), encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-Manifest", str(manifest), "-Lang", lang],
            capture_output=True, timeout=40 + 4 * len(jobs), creationflags=flags)
        if proc.returncode == EXIT_NO_VOICE:
            return ["no_voice"] * len(jobs)
        if proc.returncode != 0:
            return ["No se pudo generar la voz (PowerShell terminó con error)."] * len(jobs)
        ok = {int(m.group(1)) for m in re.finditer(r"^OK (\d+)", proc.stdout.decode("utf-8", "replace"), re.M)}
        out = []
        for i, (_t, path) in enumerate(jobs):
            tmp = tmp_paths[i]
            if i in ok and tmp.exists() and tmp.stat().st_size > 100:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, path)
                out.append(None)
            else:
                out.append("No se pudo generar la voz de este texto.")
        return out
    except FileNotFoundError:
        return ["No se encontró PowerShell para generar la voz."] * len(jobs)
    except subprocess.TimeoutExpired:
        return ["La voz de Windows tardó demasiado."] * len(jobs)
    except Exception as exc:  # noqa: BLE001
        return [f"No se pudo generar la voz: {exc}"] * len(jobs)
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


def windows_synth(text: str, lang: str, out_path: Path):
    """Sintetiza un texto en `out_path` (.wav). Devuelve None si salió bien o un código/mensaje de error."""
    return windows_synth_batch([(text, out_path)], lang)[0]


def wav_to_mp3(wav_path: Path, mp3_path: Path) -> bool:
    """Convierte un .wav (PCM de 16 bits) a MP3 de 32 kbps (≈10 veces más pequeño). Necesita `pip install lameenc`;
    False si no está o si el audio no es PCM de 16 bits."""
    try:
        import lameenc
        import wave
        with wave.open(str(wav_path), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            pcm = w.readframes(w.getnframes())
        if width != 2 or channels not in (1, 2) or not pcm:
            return False
        enc = lameenc.Encoder()
        enc.set_bit_rate(32 if channels == 1 else 64)
        enc.set_in_sample_rate(rate)
        enc.set_channels(channels)
        enc.set_quality(2)
        data = bytes(enc.encode(pcm)) + bytes(enc.flush())
        Path(mp3_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(mp3_path) + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, mp3_path)
        return True
    except Exception:  # noqa: BLE001
        return False


def winsound_play(path: Path):
    import winsound
    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)


def winsound_stop():
    import winsound
    winsound.PlaySound(None, winsound.SND_PURGE)


def card_texts(card, lang: str = "") -> tuple:
    """(palabra, frase) que se leen en voz alta para una tarjeta: de la palabra, en japonés su lectura en kana (evita errores
    con los kanji); de la frase, el original. Ya limpios (cadena vacía si no hay)."""
    lang = lang or getattr(card, "lang", "") or "en"
    word = (getattr(card, "reading", "") or card.word) if lang == "ja" else card.word
    return clean_text(word, lang), clean_text(getattr(card, "sentence", ""), lang)


def prune_cache(folder: Path, max_mb: int = MAX_CACHE_MB):
    """Borra los audios más viejos si la caché pasa de `max_mb` (los .wav; los .mp3 de exportación pesan casi nada)."""
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

    def __init__(self, cache_dir: Path = CACHE_DIR, synth=None, play=None, stop_play=None, parent=None, synth_batch=None):
        super().__init__(parent)
        self.cache_dir = Path(cache_dir)
        windows = is_windows()
        self._synth_batch = synth_batch or (windows_synth_batch if windows and synth is None else None)
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

    # --- lotes (exportación a Anki) ---
    def ensure(self, texts, lang: str, progress=None, chunk: int = 25):
        """Deja en la caché el audio de todos los `texts` (sin repetidos; lo que ya está no se vuelve a generar) con pocas llamadas
        a PowerShell. Pensado para un hilo de trabajo. Devuelve ({texto limpio: ruta .wav}, error o ""). `progress(hechos, total)`."""
        cleaned = list(dict.fromkeys(t for t in (clean_text(x, lang) for x in texts) if t))
        paths, err = {}, ""
        todo = []
        for t in cleaned:
            p = self.path_for(t, lang)
            if p.exists() and p.stat().st_size > 100:
                paths[t] = p
            else:
                todo.append(t)
        done = len(paths)
        if progress:
            progress(done, len(cleaned))
        if todo and not self.available:
            return paths, "La voz solo está disponible en Windows."
        for i in range(0, len(todo), chunk):
            part = todo[i:i + chunk]
            jobs = [(t, self.path_for(t, lang)) for t in part]
            try:
                if self._synth_batch is not None:
                    errors = self._synth_batch(jobs, lang)
                else:
                    errors = [self._synth(t, lang, p) for t, p in jobs]
            except Exception as exc:  # noqa: BLE001
                errors = [f"No se pudo generar la voz: {exc}"] * len(jobs)
            for (t, p), e in zip(jobs, errors):
                if e:
                    err = err or (no_voice_message(lang) if e == "no_voice" else str(e))
                elif p.exists():
                    paths[t] = p
            if err and all(e == "no_voice" for e in errors):
                break                           # sin voz del idioma: no tiene sentido seguir
            done += len(part)
            if progress:
                progress(done, len(cleaned))
        return paths, err

    def media_files(self, paths: dict, lang: str) -> tuple:
        """Archivos para adjuntar a Anki a partir de {texto: .wav de la caché}. MP3 de 32 kbps si está `lameenc` (≈10 veces
        más pequeños); si no, el .wav. Los nombres (`ocrt_<idioma>_<clave>.<ext>`) dependen del texto: reexportar los reutiliza.
        Devuelve ({texto: ruta}, cuántos se convirtieron a MP3)."""
        out, mp3_count = {}, 0
        folder = self.cache_dir / "anki"
        for text, wav in paths.items():
            stem = f"ocrt_{lang}_{cache_key(text, lang)}"
            mp3 = folder / f"{stem}.mp3"
            if mp3.exists() and mp3.stat().st_size > 100:
                out[text] = mp3
                mp3_count += 1
            elif wav_to_mp3(wav, mp3):
                out[text] = mp3
                mp3_count += 1
            else:
                dst = folder / f"{stem}.wav"
                if not dst.exists():
                    folder.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(wav, dst)
                out[text] = dst
        return out, mp3_count

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
