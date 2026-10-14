"""Traducción local EN -> ES con CTranslate2 y Opus-MT
"""
import re
from pathlib import Path

import ctranslate2
import sentencepiece as spm

# Conversión a CTranslate2 de Helsinki-NLP/opus-mt-en-es (comunitaria, en Hugging Face).
# Verificada: incluye model.bin, shared_vocabulary.txt, source.spm y target.spm.
REPO_ID = "michaelfeil/ct2fast-opus-mt-en-es"
MODEL_DIR = Path(__file__).resolve().parent / "models" / "ct2fast-opus-mt-en-es"

# Si una línea termina así, se respeta el salto de línea; si no, se une con la siguiente.
_END_PUNCT = (".", "!", "?", "…", ":", '"', "”", "»", ")", "]")
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def join_wrapped_lines(text: str) -> str:
    """Une las líneas que el cuadro de diálogo partió a mitad de frase.

    El OCR devuelve una línea por renglón visual, pero una frase puede ocupar
    varios renglones. Traducirlos por separado rompe el sentido, así que solo
    se conserva un salto de línea cuando la línea anterior terminó en puntuación.
    (En menús sin puntuación esto los une; para eso existe la casilla en la UI.)
    """
    paragraphs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if paragraphs and not paragraphs[-1].endswith(_END_PUNCT):
            paragraphs[-1] += " " + line
        else:
            paragraphs.append(line)
    return "\n".join(paragraphs)


class Translator:
    """Carga el modelo una sola vez; reutilízalo para todas las traducciones."""

    def __init__(self, model_dir: Path = MODEL_DIR, repo_id: str = REPO_ID,
                 cpu_threads: int = 2, beam_size: int = 4):
        self.model_dir = Path(model_dir)
        self.repo_id = repo_id
        self.cpu_threads = cpu_threads  # menos hilos = menos CPU mientras juegas
        self.beam_size = beam_size
        self._ct2 = None
        self._sp_src = None
        self._sp_tgt = None

    @property
    def ready(self) -> bool:
        return self._ct2 is not None

    def _missing_files(self) -> list:
        """Archivos imprescindibles que faltan en la carpeta del modelo."""
        missing = [f for f in ("model.bin", "source.spm", "target.spm")
                   if not (self.model_dir / f).exists()]
        if not any((self.model_dir / f).exists()
                   for f in ("shared_vocabulary.txt", "shared_vocabulary.json")):
            missing.append("shared_vocabulary.txt")
        return missing

    def load(self):
        if self._missing_files():
            self._download()
            missing = self._missing_files()
            if missing:  # error claro en vez de un fallo críptico de CTranslate2
                present = sorted(p.name for p in self.model_dir.iterdir())
                raise RuntimeError(
                    f"Faltan archivos del modelo tras la descarga: {missing}. "
                    f"Repositorio: {self.repo_id}. Archivos presentes: {present}"
                )
        self._ct2 = ctranslate2.Translator(
            str(self.model_dir),
            device="cpu",
            compute_type="int8",   # cuantizado: menos RAM y más rápido en CPU
            inter_threads=1,
            intra_threads=self.cpu_threads,
        )
        self._sp_src = spm.SentencePieceProcessor(model_file=str(self.model_dir / "source.spm"))
        self._sp_tgt = spm.SentencePieceProcessor(model_file=str(self.model_dir / "target.spm"))
        self.translate("Hello")  # calentamiento

    def _download(self):
        from huggingface_hub import snapshot_download  # import tardío: solo la 1ª vez
        self.model_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=self.repo_id, local_dir=str(self.model_dir))

    def translate(self, text: str, merge_lines: bool = True) -> str:
        if not self.ready:
            raise RuntimeError("El traductor no está cargado.")
        if merge_lines:
            text = join_wrapped_lines(text)

        # Cada frase se traduce por separado: mejor calidad y evita truncar textos largos.
        sentences, layout = [], []
        for line in (ln.strip() for ln in text.splitlines()):
            if not line:
                continue
            parts = [s for s in _SENT_SPLIT.split(line) if s]
            layout.append(len(parts))
            sentences.extend(parts)
        if not sentences:
            return ""

        batch = [self._sp_src.encode(s, out_type=str) + ["</s>"] for s in sentences]
        results = self._ct2.translate_batch(
            batch, beam_size=self.beam_size, max_decoding_length=256
        )
        translated = [self._sp_tgt.decode(r.hypotheses[0]).strip() for r in results]

        # Reconstruir las líneas originales
        out, i = [], 0
        for n in layout:
            out.append(" ".join(translated[i:i + n]))
            i += n
        return "\n".join(out)


if __name__ == "__main__":
    import time

    print("Cargando traductor (la primera vez descarga el modelo)...")
    t0 = time.time()
    tr = Translator()
    tr.load()
    print(f"Cargado en {time.time() - t0:.1f} s\n")

    sample = "You can't open this door yet.\nFind the rusty key first,\nthen come back."
    t0 = time.time()
    print(tr.translate(sample))
    print(f"\nTraducido en {time.time() - t0:.2f} s")