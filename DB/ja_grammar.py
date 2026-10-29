"""Formas gramaticales y contracciones del japonés (ている, なきゃ, たくなかった...).

Capa EXTRA sobre el diccionario: al pasar el mouse sobre una pieza de una forma, el popup muestra
arriba una sección de «gramática» y debajo el diccionario de siempre. Nada del diccionario cambia.

Las formas están en data/grammar_ja.tsv (editable; se relee solo cuando guardas el archivo):

    Patrón  Coincide  Significado  Se une a  Registro  Ejemplo  Traducción  Nivel  Notas

«Coincide» describe la secuencia de piezas (tokens de Sudachi) de la forma. Varias alternativas se
separan con « ; ». Cada alternativa son piezas separadas por espacios:

    @VAX          (opcional, primero) la pieza ANTERIOR debe ser de esa clase, sin formar parte de la
                  forma: V verbo, A adjetivo-i, X auxiliar, N sustantivo, S adjetivo-na, P partícula
    て|で          alternativas de una pieza: coincide con lo escrito o con su forma de diccionario
    =て            solo con lo escrito
    +よう          pieza conjugada (lema distinto) cuya escritura termina en «よう» (食べよう)
    ~すぎる        pieza cuya escritura termina en «すぎる» y es más larga (食べすぎる)
    ん:助詞        …y además su categoría en Sudachi debe ser esa
"""
import csv
import functools
from dataclasses import dataclass, field
from pathlib import Path

from DB.ja_nlp import SKIP_POS, tokenizer

GRAMMAR_PATH = Path(__file__).resolve().parent / "data" / "grammar_ja.tsv"
MAX_SHOWN = 3                   # formas distintas que se muestran por palabra

_CLASSES = {"V": "動詞", "A": "形容詞", "X": "助動詞", "N": "名詞", "S": "形状詞", "P": "助詞"}


@dataclass
class _Piece:
    options: list                # [(modo, texto)] modo: "any" | "=" | "+" | "~"
    pos: str = ""


@dataclass
class _Alt:
    context: frozenset           # categorías Sudachi permitidas para la pieza anterior ("" = sin requisito)
    pieces: list


@dataclass
class GrammarEntry:
    order: int
    pattern: str
    alts: list
    meaning: str = ""
    attaches: str = ""
    register: str = ""
    example: str = ""
    example_en: str = ""
    level: str = ""
    notes: str = ""


@dataclass
class GrammarMatch:
    entry: GrammarEntry
    start: int                   # posiciones en el texto
    end: int
    text: str


def _parse_piece(spec: str) -> _Piece:
    pos = ""
    if ":" in spec:
        spec, pos = spec.rsplit(":", 1)
    options = []
    for alt in spec.split("|"):
        mode = "any"
        if alt[:1] in "=+~":
            mode, alt = alt[0], alt[1:]
        if alt:
            options.append((mode, alt))
    return _Piece(options, pos)


def parse_match(text: str) -> list:
    alts = []
    for chunk in text.split(";"):
        words = chunk.split()
        if not words:
            continue
        context = frozenset()
        if words[0].startswith("@"):
            context = frozenset(_CLASSES[c] for c in words[0][1:] if c in _CLASSES)
            words = words[1:]
        if words:
            alts.append(_Alt(context, [_parse_piece(w) for w in words]))
    return alts


def _piece_matches(piece: _Piece, tok) -> bool:
    if piece.pos and tok.pos != piece.pos:
        return False
    lemma = tok.lemma or tok.surface
    for mode, text in piece.options:
        if mode == "any" and text == "*":
            return True
        if mode == "=" and tok.surface == text:
            return True
        if mode == "any" and (tok.surface == text or lemma == text):
            return True
        if mode == "~" and tok.surface.endswith(text) and len(tok.surface) > len(text):
            return True
        if mode == "+" and tok.lemma and tok.surface.endswith(text) and len(tok.surface) > len(text):
            return True
    return False


class GrammarBook:
    """Las formas del .tsv. Se relee solo si el archivo cambia."""

    def __init__(self, path: Path = GRAMMAR_PATH):
        self.path = Path(path)
        self._mtime = None
        self.entries = []
        self.version = 0

    def _load(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime, self.entries = None, []
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        entries = []
        with open(self.path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader((ln for ln in f if not ln.startswith("#")), delimiter="\t"):
                pattern = (row.get("Patrón") or "").strip()
                alts = parse_match(row.get("Coincide") or "")
                if pattern and alts:
                    entries.append(GrammarEntry(
                        len(entries), pattern, alts, (row.get("Significado") or "").strip(),
                        (row.get("Se une a") or "").strip(), (row.get("Registro") or "").strip(),
                        (row.get("Ejemplo") or "").strip(), (row.get("Traducción") or "").strip(),
                        (row.get("Nivel") or "").strip(), (row.get("Notas") or "").strip()))
        self.entries = entries
        self.version += 1
        self._cache_clear()

    def _cache_clear(self):
        self._find.cache_clear()

    @property
    def available(self) -> bool:
        self._load()
        return bool(self.entries)

    @functools.lru_cache(maxsize=256)
    def _find(self, text: str) -> tuple:
        toks = tokenizer.tokens(text)
        found = []
        for i in range(len(toks)):
            for entry in self.entries:
                for alt in entry.alts:
                    n = len(alt.pieces)
                    if i + n > len(toks):
                        continue
                    if alt.context:
                        prev = toks[i - 1] if i else None
                        if prev is None or prev.pos not in alt.context:
                            continue
                    if all(_piece_matches(p, toks[i + k]) for k, p in enumerate(alt.pieces)):
                        found.append(GrammarMatch(entry, toks[i].start, toks[i + n - 1].end,
                                                  text[toks[i].start:toks[i + n - 1].end]))
                        break
        return tuple(found)

    def all_matches(self, text: str) -> list:
        self._load()
        if not self.entries or not tokenizer.available:
            return []
        return list(self._find(text))

    def matches_at(self, text: str, index: int) -> list:
        """Formas que contienen la posición `index` del texto: la más larga primero; no se repiten
        las que quedan dentro de otra ya elegida."""
        covering = [m for m in self.all_matches(text) if m.start <= index < m.end]
        covering.sort(key=lambda m: (-(m.end - m.start), m.entry.order))
        chosen = []
        for m in covering:
            if any(c.start <= m.start and m.end <= c.end for c in chosen):
                continue
            chosen.append(m)
            if len(chosen) == MAX_SHOWN:
                break
        return chosen


book = GrammarBook()


def grammar_entries(text: str, index: int) -> list:
    """Entradas listas para el popup (dictionary.Entry de tipo «grammar») de las formas en `index`."""
    from DB.dictionary import Entry, Sense
    out = []
    for m in book.matches_at(text, index):
        e = m.entry
        bits = [b for b in (m.text if m.text != e.pattern else "", e.attaches, e.register,
                            f"JLPT {e.level}" if e.level else "", e.notes) if b]
        example = " — ".join(x for x in (e.example, e.example_en) if x)
        out.append(Entry(lemma=e.pattern, senses=[Sense(pos="gramática", definition=e.meaning, example=example)],
                         total=1, kind="grammar", note=" · ".join(bits)))
    return out
