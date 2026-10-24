"""Reglas de texto para japonés: dividir en frases y unir líneas SIN espacios.

Mismo contrato que sentences.py (devuelve Sentence con posiciones en el texto original),
para que el traductor y el clic sobre una palabra coincidan en qué es «la frase».

Reglas:
- Se corta siempre tras 。！？!? (junto con los cierres que los siguen: 」』）】”).
- «……» / «…» en mitad de una frase NO corta; solo corta si termina la línea.
- Con «unir líneas» un salto de línea solo separa frases si la línea anterior termina en
  puntuación o cierre (。！？…」』）】). Sin unir, cada salto separa.
- Al unir, no se mete espacio entre caracteres japoneses; solo entre dos palabras latinas.
"""
import re

from Cards.sentences import Sentence

HARD_END = "。！？!?"
ELLIPSIS = "…‥"
CLOSERS = "」』）)】〕》〉”’\"'"
LINE_END = set(HARD_END + ELLIPSIS + CLOSERS)
_LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85")
_WS = re.compile(r"\s+")
_LATIN = re.compile(r"[A-Za-z0-9]")


def join_ja(text: str) -> str:
    """Quita saltos y espacios entre caracteres japoneses; deja uno entre palabras latinas."""
    parts = _WS.split(text.strip())
    out = parts[0] if parts and parts[0] else ""
    for p in parts[1:]:
        if not p:
            continue
        out += (" " if out and _LATIN.match(out[-1]) and _LATIN.match(p[0]) else "") + p
    return out


def split_sentences_ja(text: str, merge_lines: bool = True) -> list:
    cuts = []
    n, i = len(text), 0
    while i < n:
        c = text[i]
        if c in HARD_END:
            j = i + 1
            while j < n and (text[j] in HARD_END or text[j] in ELLIPSIS or text[j] in CLOSERS):
                j += 1
            cuts.append(j)
            i = j
            continue
        if c in _LINE_BREAKS:
            k = i - 1
            while k >= 0 and text[k].isspace():
                k -= 1
            if k >= 0 and (not merge_lines or text[k] in LINE_END):
                cuts.append(i)
        i += 1
    cuts.append(n)

    sentences, pos = [], 0
    for cut in cuts:
        raw = text[pos:cut]
        lead = len(raw) - len(raw.lstrip())
        a, b = pos + lead, cut - (len(raw) - len(raw.rstrip()))
        if a < b:
            sentences.append(Sentence(a, b, join_ja(text[a:b])))
        pos = max(pos, cut)
    return sentences


def join_wrapped_lines_ja(text: str) -> str:
    """Como translator.join_wrapped_lines, pero sin espacios entre líneas japonesas."""
    paragraphs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if paragraphs and paragraphs[-1][-1] not in LINE_END:
            paragraphs[-1] = join_ja(paragraphs[-1] + "\n" + line)
        else:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def sentence_at_ja(text: str, index: int, merge_lines: bool = True):
    for s in split_sentences_ja(text, merge_lines):
        if s.start <= index < s.end:
            return s.text
    return None
