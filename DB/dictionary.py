"""Consulta del diccionario local: SQLite (WordNet + español) y glosario de palabras funcionales.

    d = Dictionary()
    if d.available:
        result = d.lookup("running")      # LookupResult o None si no hay entrada
        for entry in result.entries:      # p. ej. "running" y "run"
            for sense in entry.senses: ...

La base SQLite se genera una vez con build_dictionary.py. El glosario de artículos,
pronombres, preposiciones, conjunciones y auxiliares está en function_words.py.
"""
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from DB.function_words import ALSO_CONTENT, CONTRACTIONS, FORMS, FUNCTION_WORDS

DB_PATH = Path(__file__).resolve().parent / "data" / "dictionary.db"

MAX_LEMMAS = 3             # candidatos máximos por palabra (p. ej. "better" -> better, good, well)
SENSES_SINGLE = 4          # significados mostrados cuando solo hay un candidato
SENSES_MULTI = (2, 1, 1)   # significados por candidato, en orden, cuando hay varios
SENSES_AFTER_GLOSSARY = 1  # significados de WordNet tras el glosario (will, can, may...)

# Reglas de lematización de WordNet (morphy), una sola pasada, igual que NLTK.
_SUBST = {
    "n": [("s", ""), ("ses", "s"), ("ves", "f"), ("xes", "x"), ("zes", "z"),
          ("ches", "ch"), ("shes", "sh"), ("men", "man"), ("ies", "y")],
    "v": [("s", ""), ("ies", "y"), ("es", "e"), ("es", ""), ("ed", "e"), ("ed", ""),
          ("ing", "e"), ("ing", "")],
    "a": [("er", ""), ("est", ""), ("er", "e"), ("est", "e")],
    "r": [],
}

POS_LABEL = {"n": "sust.", "v": "verbo", "a": "adj.", "r": "adv."}

_MODALS = frozenset("can could will would shall should may might must ought".split())
_VERB_CLITICS = ("n't", "'ll", "'ve", "'re", "'d", "'m")   # contracciones de verbos


@dataclass
class Sense:
    pos: str                                   # n, v, a, r (WordNet) o la categoría del glosario
    definition: str
    example: str = ""
    synonyms: list = field(default_factory=list)
    antonyms: list = field(default_factory=list)
    spanish: list = field(default_factory=list)


@dataclass
class Entry:
    lemma: str                                 # forma base: "run"
    senses: list                               # los más comunes (recortados)
    total: int                                 # significados totales en el diccionario
    kind: str = "word"                         # "word" (WordNet) o "function" (glosario)
    note: str = ""                             # p. ej. "pasado, con I / he / she / it"


@dataclass
class LookupResult:
    query: str                                 # lo que se escribió: "running"
    entries: list


_ATTRIBUTION = re.compile(r"\s*;\s*-\s*[A-Z][^;]*$")   # "...; - George Meredith"


def _clean_definition(text: str) -> str:
    """Quita restos de WordNet: atribuciones de citas y punto y coma sueltos."""
    text = _ATTRIBUTION.sub("", text)
    text = re.sub(r";(\s*;)+", ";", text)
    return re.sub(r"[;,\s]+$", "", text)


def _split(value: str) -> list:
    return value.split("|") if value else []


def _normalize(token: str) -> str:
    return token.lower().replace("’", "'").strip("'-")


def _function_entries(t: str):
    """([Entry], también_buscar_en_WordNet) para una palabra ya normalizada del glosario."""
    if t in CONTRACTIONS:
        expansion, spanish, example = CONTRACTIONS[t]
        sense = Sense("contracción", f"Contracción de «{expansion}».", example, spanish=[spanish])
        return [Entry(t, [sense], 1, kind="function")], False
    head, note = FORMS.get(t, (t, ""))
    uses = FUNCTION_WORDS.get(head)
    if not uses:
        return [], False
    senses = [Sense(cat, explanation, example, spanish=[spanish])
              for cat, spanish, explanation, example in uses]
    return [Entry(head, senses, len(senses), kind="function", note=note)], head in ALSO_CONTENT


def _forms(token: str) -> list:
    """[(forma, categorías)] a buscar en WordNet para un token.

    Contracciones: "mightn't" -> "might" solo como verbo; "John's" -> "john" (cualquier categoría).
    """
    t = _normalize(token)
    if not t:
        return []
    forms = [(t, "nvar")]
    for clitic in _VERB_CLITICS:
        if t.endswith(clitic) and len(t) > len(clitic):
            base = t[: -len(clitic)]
            if base not in _MODALS:
                forms.append((base, "v"))
            break
    else:
        if t.endswith("'s") and len(t) > 2:
            forms.append((t[:-2], "nvar"))
    return forms


class Dictionary:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path).resolve()
        self._con = None
        self._exists_cache = {}
        self._exc_cache = {}
        if self.path.exists():
            # Solo lectura: nunca modifica la base.
            self._con = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)

    @property
    def available(self) -> bool:
        return self._con is not None

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None

    # --- consultas básicas (con caché) ---
    def _exists(self, lemma: str, pos: str) -> bool:
        key = (lemma, pos)
        if key not in self._exists_cache:
            row = self._con.execute(
                "SELECT 1 FROM senses WHERE lemma=? AND pos=? LIMIT 1", key).fetchone()
            self._exists_cache[key] = row is not None
        return self._exists_cache[key]

    def _exceptions(self, form: str, pos: str) -> list:
        key = (form, pos)
        if key not in self._exc_cache:
            rows = self._con.execute(
                "SELECT lemma FROM exceptions WHERE form=? AND pos=?", key).fetchall()
            self._exc_cache[key] = [r[0] for r in rows]
        return self._exc_cache[key]

    # --- lematización (algoritmo morphy de WordNet) ---
    def _morphy(self, form: str, pos: str) -> list:
        excs = self._exceptions(form, pos)
        if excs:                                   # formas irregulares: ran -> run
            forms = excs
        else:                                      # reglas de sufijos, una sola pasada
            forms = [form[: -len(old)] + new
                     for old, new in _SUBST[pos] if form.endswith(old)]
        out = []
        for f in [form] + forms:
            if f not in out and self._exists(f, pos):
                out.append(f)
        return out

    def candidate_lemmas(self, form: str, pos: str = "nvar") -> list:
        """Lemas posibles de una forma ("running" -> ["running", "run"]), exacta primero."""
        out = [form] if any(self._exists(form, p) for p in pos) else []
        for p in pos:
            for lemma in self._morphy(form, p):
                if lemma not in out:
                    out.append(lemma)
        return out

    # --- API pública ---
    def lookup(self, word: str, compound: str = ""):
        """Busca una palabra. `compound` es el compuesto con guiones que la contiene
        ("well-known"): se prueba primero y, si no existe, se busca la palabra sola."""
        if not self.available:
            return None
        for token in (compound, word):
            if token:
                result = self._lookup_token(token)
                if result is not None:
                    return result
        return None

    def _lookup_token(self, token: str):
        entries, also_content = _function_entries(_normalize(token))
        if not entries or also_content:
            limits = (SENSES_AFTER_GLOSSARY,) if entries else None
            entries = entries + self._content_entries(token, limits)
        if not entries:
            return None
        return LookupResult(query=token, entries=entries)

    def key_for(self, word: str, compound: str = "") -> str:
        """Clave con la que se guarda (y se reconoce como ya guardada) una palabra.

        Es su lema principal: «swords» y «sword» comparten la clave «sword», y «ran» y «run»
        la clave «run». Las palabras sin entrada en el diccionario usan la propia palabra,
        en minúsculas.
        """
        for token in (compound, word):
            if not token:
                continue
            t = _normalize(token)
            if t in CONTRACTIONS:
                return t
            head, _note = FORMS.get(t, (t, ""))
            if head in FUNCTION_WORDS:
                return head
            if self.available:
                found = self._found_lemmas(token)
                if found:
                    return found[0][0].replace("_", " ")
        return _normalize(word)

    def _found_lemmas(self, token: str) -> list:
        """[(lema, categorías)] candidatos de un token, el exacto primero (máximo MAX_LEMMAS)."""
        found, seen = [], set()
        for form, pos in _forms(token):
            for lemma in self.candidate_lemmas(form, pos):
                if lemma not in seen:
                    seen.add(lemma)
                    found.append((lemma, pos))
        return found[:MAX_LEMMAS]

    def _content_entries(self, token: str, limits=None) -> list:
        found = self._found_lemmas(token)
        if limits is None:
            limits = (SENSES_SINGLE,) if len(found) == 1 else SENSES_MULTI
        return [self._entry(lemma, limits[min(i, len(limits) - 1)], pos)
                for i, (lemma, pos) in enumerate(found)]

    def _entry(self, lemma: str, limit: int, pos: str) -> Entry:
        marks = ",".join("?" * len(pos))
        rows = self._con.execute(
            "SELECT pos, definition, example, synonyms, antonyms, spanish "
            f"FROM senses WHERE lemma=? AND pos IN ({marks}) ORDER BY rank LIMIT ?",
            (lemma, *pos, limit)).fetchall()
        total = self._con.execute(
            f"SELECT COUNT(*) FROM senses WHERE lemma=? AND pos IN ({marks})",
            (lemma, *pos)).fetchone()[0]
        senses = [Sense(p, _clean_definition(definition), example, _split(syn), _split(ant), _split(es))
                  for p, definition, example, syn, ant, es in rows]
        return Entry(lemma=lemma.replace("_", " "), senses=senses, total=total)