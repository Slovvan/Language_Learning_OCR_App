"""Segmentación del texto OCR en frases, con la misma regla que usa el traductor.

Sirve para saber a qué frase pertenece una palabra en la que se hizo clic. Si esta
regla y la del traductor no coincidieran, la «frase de ejemplo» de una tarjeta no
sería la misma que se tradujo en el cuadro de abajo.

Regla (idéntica a la de translator.py):
- Con «unir líneas» activado, un salto de línea solo separa frases si la línea anterior
  termina en puntuación (. ! ? … : comillas de cierre, paréntesis o corchete de cierre).
- Con «unir líneas» desactivado, cada salto de línea separa frases.
- Además, siempre se separa tras . ! ? … seguidos de espacio.
"""
import re
from dataclasses import dataclass

SENT_END = ".!?…"                       # separa frases aunque no haya salto de línea
END_PUNCT = SENT_END + ':"”»)]'         # si una línea termina así, su salto de línea se respeta
_LINE_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85  ")   # los que usa str.splitlines()
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Sentence:
    start: int      # posición en el texto original (incluida)
    end: int        # posición en el texto original (excluida)
    text: str       # la frase con los espacios y saltos de línea reducidos a un espacio


def split_sentences(text: str, merge_lines: bool = True) -> list:
    """Divide `text` en frases, devolviendo también su posición en el texto original."""
    cuts = []                            # (inicio, fin) de cada separación entre frases
    for m in _WS.finditer(text):
        s, e = m.span()
        if s == 0 or e == len(text):
            continue
        prev = text[s - 1]               # último carácter visible antes de la separación
        has_break = any(c in _LINE_BREAKS for c in m.group())
        if prev in SENT_END or (has_break and (not merge_lines or prev in END_PUNCT)):
            cuts.append((s, e))
    cuts.append((len(text), len(text)))

    sentences, pos = [], 0
    for cut_start, cut_end in cuts:
        raw = text[pos:cut_start]
        lead = len(raw) - len(raw.lstrip())
        a, b = pos + lead, cut_start - (len(raw) - len(raw.rstrip()))
        if a < b:
            sentences.append(Sentence(a, b, " ".join(text[a:b].split())))
        pos = cut_end
    return sentences


def sentence_at(text: str, index: int, merge_lines: bool = True):
    """Frase (texto) que contiene la posición `index` de `text`, o None si no hay."""
    for s in split_sentences(text, merge_lines):
        if s.start <= index < s.end:
            return s.text
    return None