"""Reglas propias de la traducción japonés -> inglés: glosario editable y punto final inteligente.

Lo que descubrimos con tus frases reales (compare_ja_variants.py):
- Los fragmentos con verbo sin puntuación salen mejor con «。» al final
  («経験値を獲得した»: «Empirical» -> «I got experience.»), pero las etiquetas de menú sufren
  («オプション»: «Option» -> «Optional.»). Por eso el punto solo se añade si parece una oración.
- Algunos términos conviene fijarlos tú: data/glossary_ja.tsv (ver abajo).

Glosario (data/glossary_ja.tsv, UTF-8, una línea por término, columnas separadas por tabulador):
    コンティニュー<TAB>Continue
    所持金<TAB>Money
- Si TODA la línea capturada es un término (ignorando «！», «？», «。» finales), se usa tu traducción
  tal cual, sin pasar por el modelo.
- Si el término va dentro de una frase, se sustituye por tu traducción antes de traducir.
Las líneas que empiezan con # son comentarios. Se relee solo cuando guardas el archivo.
"""
import os
import re
from pathlib import Path

from Translator.ja_text import LINE_END

GLOSSARY_PATH = Path(__file__).resolve().parent / "data" / "glossary_ja.tsv"
_TRAILING = re.compile(r"[\s。！？!?…‥.]+$")
_SENTENCE_POS = frozenset({"動詞", "助動詞", "形容詞"})     # el texto termina en esto -> es una oración
_ASCII_END = ".!?…"


class Glossary:
    def __init__(self, path: Path = GLOSSARY_PATH):
        self.path = Path(path)
        self._mtime = None
        self._terms = {}                    # término -> traducción
        self._ordered = []                  # términos, los más largos primero

    @property
    def version(self):
        """Cambia cuando editas el archivo (para vaciar la caché de traducciones)."""
        try:
            return os.stat(self.path).st_mtime_ns
        except OSError:
            return 0

    def _load(self):
        mtime = self.version
        if mtime == self._mtime:
            return
        self._mtime, self._terms = mtime, {}
        try:
            for line in self.path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                src, sep, dst = line.partition("\t")
                if not sep:
                    src, sep, dst = line.partition("=")
                if sep and src.strip() and dst.strip():
                    self._terms[src.strip()] = dst.strip()
        except OSError:
            pass
        self._ordered = sorted(self._terms, key=len, reverse=True)

    def exact(self, text: str):
        """Traducción fija si toda la línea es un término; conserva «！» y «？» finales."""
        self._load()
        if not self._terms:
            return None
        core = _TRAILING.sub("", text.strip())
        if core not in self._terms:
            return None
        tail = text.strip()[len(core):]
        mark = "!" if "！" in tail or "!" in tail else "?" if "？" in tail or "?" in tail else ""
        return self._terms[core] + mark

    def substitute(self, text: str) -> str:
        self._load()
        for term in self._ordered:
            if term in text:
                text = text.replace(term, self._terms[term])
        return text


glossary = Glossary()


def looks_like_sentence(text: str) -> bool:
    """¿Parece una oración (verbo al final o partículas) y no una etiqueta de menú?"""
    from DB.ja_nlp import tokenizer
    tokens = [t for t in tokenizer.tokens(text) if t.pos not in ("補助記号", "空白", "記号")]
    if not tokens:
        return False
    if tokens[-1].pos in _SENTENCE_POS:
        return True
    return any(t.pos == "助詞" for t in tokens[:-1])        # «経験値を…», «MPが…»


def needs_period(text: str) -> bool:
    text = text.strip()
    if not text or text[-1] in LINE_END or text[-1] in _ASCII_END:
        return False
    return looks_like_sentence(text)


def prepare(text: str) -> str:
    """Entrada que se le da al modelo: punto final si es una oración y términos del glosario."""
    period = needs_period(text)
    text = glossary.substitute(text)
    return text + "。" if period else text


def fixed(text: str):
    return glossary.exact(text)


def version():
    return glossary.version
