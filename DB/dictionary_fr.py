"""Consulta del diccionario francés → español (SQLite + glosario de palabras funcionales).

    d = FrDictionary()
    if d.available:
        r = d.lookup("chevaux")          # LookupResult con la entrada «cheval»

La base (data/dictionary_fr.db) se genera una vez con build_dictionary_fr.py. Los lemas se
obtienen con `simplemma` (pip install simplemma; sin él solo se busca la forma exacta).
Misma interfaz que Dictionary (inglés) y JaDictionary (japonés).
"""
import re
import sqlite3
import unicodedata
from pathlib import Path

from DB.dictionary import Entry, LookupResult, Sense, _split
from DB.fr_function_words import ALSO_CONTENT_FR, ELISIONS, FORMS_FR, FUNCTION_WORDS_FR

DB_PATH = Path(__file__).resolve().parent / "data" / "dictionary_fr.db"

MAX_LEMMAS = 3
SENSES_SINGLE = 6
SENSES_MULTI = (3, 2, 1)
SENSES_AFTER_GLOSSARY = 2
FULL_SINGLE = 30
FULL_MULTI = (20, 8, 5)
FULL_AFTER_GLOSSARY = 8
MAX_DEFINITION = 160         # las definiciones largas se recortan


def normalize(token: str) -> str:
    return token.lower().replace("’", "'").replace("‘", "'").strip("-").strip()


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).replace("œ", "oe").replace("æ", "ae")


_lemmatizer = None


def _lemmas_of(form: str) -> list:
    """Lemas de simplemma para una forma ('chevaux' -> ['cheval']); [] si no está instalado."""
    global _lemmatizer
    if _lemmatizer is None:
        try:
            import simplemma
            _lemmatizer = simplemma.lemmatize
        except ImportError:
            _lemmatizer = False
    if not _lemmatizer:
        return []
    out = []
    for greedy in (False, True):
        try:
            lemma = _lemmatizer(form, lang="fr", greedy=greedy)
        except TypeError:                         # versiones sin `greedy`
            lemma = _lemmatizer(form, lang="fr")
        if lemma and lemma != form and lemma not in out:
            out.append(lemma)
    return out


def _function_entries(t: str):
    """([Entry], también_buscar_en_el_diccionario) para una palabra normalizada."""
    if t.endswith("'") and len(t) > 1:                     # «l'», «qu'»…
        full = ELISIONS.get(t[:-1])
        if not full:
            return [], False
        entries = _function_entries(full)[0]
        for e in entries:
            e.note = f"«{t}» es la elisión de «{full}» ante vocal o h muda"
        return entries, False
    head, note = FORMS_FR.get(t, (t, ""))
    uses = FUNCTION_WORDS_FR.get(head)
    if not uses:
        return [], False
    senses = [Sense(cat, explanation, example, spanish=[s.strip() for s in spanish.split(",")])
              for cat, spanish, explanation, example in uses]
    conjugated = t in FORMS_FR                             # «est», «ai»: solo el glosario (no «est» = este)
    return [Entry(head, senses, len(senses), kind="function", note=note)], (not conjugated) and (
        head in ALSO_CONTENT_FR or t in ALSO_CONTENT_FR)


class FrDictionary:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path).resolve()
        self._con = None
        self._exists_cache = {}
        if self.path.exists():
            self._con = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)

    @property
    def available(self) -> bool:
        return self._con is not None

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None

    def _exists(self, lemma: str) -> bool:
        if lemma not in self._exists_cache:
            self._exists_cache[lemma] = self._con.execute(
                "SELECT 1 FROM senses WHERE lemma=? LIMIT 1", (lemma,)).fetchone() is not None
        return self._exists_cache[lemma]

    def candidate_lemmas(self, token: str) -> list:
        """Lemas posibles de una forma, la exacta primero (máximo MAX_LEMMAS)."""
        t = normalize(token)
        if not t or not self.available:
            return []
        out = []

        def add(x):
            if x and x not in out and self._exists(x):
                out.append(x)

        add(t)
        for lemma in _lemmas_of(t):
            add(lemma)
        if not out:                                        # sin tildes / OCR que las pierde: «epee» -> «épée»
            for (lemma,) in self._con.execute("SELECT lemma FROM plain WHERE folded=?", (fold(t),)):
                add(lemma)
                for lm in _lemmas_of(lemma):
                    add(lm)
        if not out and "'" in t:                           # «aujourd'hui» ya lo cubre la exacta; «l'épée» suelta
            add(t.split("'")[-1])
        return out[:MAX_LEMMAS]

    # --- API pública ---
    def lookup(self, word: str, compound: str = "", full: bool = False):
        if not self.available:
            return None
        for token in (compound, word):
            if token:
                result = self._lookup_token(token, full)
                if result is not None:
                    return result
        return None

    def _lookup_token(self, token: str, full: bool = False):
        t = normalize(token)
        entries, also_content = _function_entries(t)
        if not entries or also_content:
            limits = ((FULL_AFTER_GLOSSARY if full else SENSES_AFTER_GLOSSARY),) if entries else None
            entries = entries + self._content_entries(token, limits, full)
        if not entries:
            return None
        return LookupResult(query=token, entries=entries)

    def key_for(self, word: str, compound: str = "") -> str:
        for token in (compound, word):
            if not token:
                continue
            t = normalize(token)
            if t.endswith("'") and t[:-1] in ELISIONS:
                return ELISIONS[t[:-1]]
            if t in FORMS_FR:
                return FORMS_FR[t][0]
            head = t
            if head in FUNCTION_WORDS_FR and head not in ALSO_CONTENT_FR:
                return head
            if self.available:
                found = self.candidate_lemmas(token)
                if found:
                    return found[0]
            if head in FUNCTION_WORDS_FR:
                return head
        return normalize(word)

    def _content_entries(self, token: str, limits=None, full: bool = False) -> list:
        found = self.candidate_lemmas(token)
        if limits is None:
            single, multi = (FULL_SINGLE, FULL_MULTI) if full else (SENSES_SINGLE, SENSES_MULTI)
            limits = (single,) if len(found) == 1 else multi
        return [self._entry(lemma, limits[min(i, len(limits) - 1)]) for i, lemma in enumerate(found)]

    def _entry(self, lemma: str, limit: int) -> Entry:
        rows = self._con.execute(
            "SELECT pos, definition, example, synonyms, spanish, ipa, head FROM senses "
            "WHERE lemma=? ORDER BY rank LIMIT ?", (lemma, limit)).fetchall()
        total = self._con.execute("SELECT COUNT(*) FROM senses WHERE lemma=?", (lemma,)).fetchone()[0]
        senses = []
        for pos, definition, example, syn, es, _ipa, _head in rows:
            if len(definition) > MAX_DEFINITION:
                definition = definition[:MAX_DEFINITION].rsplit(" ", 1)[0] + "…"
            senses.append(Sense(pos, definition, example, _split(syn), [], _split(es)))
        ipa = next((r[5] for r in rows if r[5]), "")          # pronunciación: /ʃval/
        head = rows[0][6] if rows and rows[0][6].lower() == lemma else lemma      # «Paris», no «paris»
        return Entry(lemma=head, senses=senses, total=total, reading=ipa)
