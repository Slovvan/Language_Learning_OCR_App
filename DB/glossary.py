"""Glosario propio: palabras que el diccionario no conoce, para rellenarlas tú.

Flujo: haces clic en una palabra sin significado -> queda «pendiente» (con su frase). Le escribes el
significado en la ventana de Pendientes (o en un .tsv que luego importas) y desde ese momento el
diccionario la muestra como cualquier otra, con prioridad sobre el diccionario normal.

Base SQLite en data/glossary.db, una lista por idioma. Se usa solo desde el hilo de la interfaz.
"""
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from DB.dictionary import Entry, LookupResult, Sense

DB_PATH = Path(__file__).resolve().parent / "data" / "glossary.db"
TSV_HEADER = ["Palabra", "Lectura", "Significado", "Frase"]
SEPARATOR = " | "          # entre significados, en el .tsv


@dataclass
class GlossaryEntry:
    id: int
    lang: str
    key: str
    word: str
    reading: str = ""
    meaning: str = ""      # un significado por línea; vacío = pendiente
    sentence: str = ""
    created: str = ""

    @property
    def pending(self) -> bool:
        return not self.meaning.strip()


class UserGlossary:
    _COLUMNS = "id, lang, key, word, reading, meaning, sentence, created"

    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, lang TEXT NOT NULL, key TEXT NOT NULL, "
            "word TEXT NOT NULL, reading TEXT NOT NULL DEFAULT '', meaning TEXT NOT NULL DEFAULT '', "
            "sentence TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, UNIQUE(lang, key))")
        self._con.commit()

    @staticmethod
    def _entry(row) -> GlossaryEntry:
        return GlossaryEntry(*row)

    # --- lectura ---
    def get(self, entry_id: int):
        row = self._con.execute(f"SELECT {self._COLUMNS} FROM entries WHERE id=?", (entry_id,)).fetchone()
        return self._entry(row) if row else None

    def by_key(self, lang: str, key: str):
        row = self._con.execute(f"SELECT {self._COLUMNS} FROM entries WHERE lang=? AND key=?",
                                (lang, key)).fetchone()
        return self._entry(row) if row else None

    def all(self, lang: str, only_pending: bool = False) -> list:
        """Entradas del idioma, la más nueva primero."""
        rows = self._con.execute(f"SELECT {self._COLUMNS} FROM entries WHERE lang=? ORDER BY id DESC",
                                 (lang,)).fetchall()
        entries = [self._entry(r) for r in rows]
        return [e for e in entries if e.pending] if only_pending else entries

    def pending_keys(self, lang: str) -> set:
        return {r[0] for r in self._con.execute(
            "SELECT key FROM entries WHERE lang=? AND TRIM(meaning)=''", (lang,))}

    def count(self, lang: str, only_pending: bool = False) -> int:
        extra = " AND TRIM(meaning)=''" if only_pending else ""
        return self._con.execute(f"SELECT COUNT(*) FROM entries WHERE lang=?{extra}", (lang,)).fetchone()[0]

    # --- escritura ---
    def add_pending(self, lang: str, key: str, word: str, sentence: str = "", reading: str = ""):
        """Marca una palabra sin significado. Devuelve la entrada nueva o None si ya estaba."""
        if self.by_key(lang, key) is not None:
            return None
        cur = self._con.execute(
            "INSERT INTO entries (lang, key, word, reading, meaning, sentence, created) VALUES (?,?,?,?,?,?,?)",
            (lang, key, word, reading, "", sentence, f"{datetime.now():%Y-%m-%d %H:%M:%S}"))
        self._con.commit()
        return self.get(cur.lastrowid)

    def update(self, entry_id: int, **fields):
        allowed = {"word", "reading", "meaning", "sentence"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            self._con.execute(f"UPDATE entries SET {sets} WHERE id=?", (*fields.values(), entry_id))
            self._con.commit()

    def delete(self, ids):
        ids = list(ids)
        if ids:
            self._con.executemany("DELETE FROM entries WHERE id=?", [(i,) for i in ids])
            self._con.commit()

    # --- búsqueda para el diccionario ---
    def lookup(self, lang: str, word: str, compound: str = "", key: str = ""):
        """LookupResult con el significado escrito por ti, o None (no existe o sigue pendiente)."""
        for k in (key, compound, word):
            if not k:
                continue
            e = self.by_key(lang, k)
            if e is not None and not e.pending:
                senses = [Sense(pos="glosario propio", definition=line.strip(),
                                spanish=[line.strip()] if lang in ("en", "fr") else [])
                          for line in e.meaning.splitlines() if line.strip()]
                return LookupResult(word, [Entry(lemma=e.word, senses=senses, total=len(senses),
                                                 note="de tu glosario", reading=e.reading)])
        return None

    # --- .tsv ---
    def export_tsv(self, lang: str, path, only_pending: bool = False) -> int:
        """Plantilla para rellenar fuera de la app: Palabra, Lectura, Significado, Frase."""
        entries = sorted(self.all(lang, only_pending), key=lambda e: e.id)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\r\n")
            w.writerow(TSV_HEADER)
            for e in entries:
                w.writerow([e.word, e.reading, SEPARATOR.join(m.strip() for m in e.meaning.splitlines() if m.strip()),
                            " ".join(e.sentence.split())])
        return len(entries)

    def import_tsv(self, lang: str, path, key_for) -> tuple:
        """Lee un .tsv (el mismo formato). Palabra nueva -> se crea; existente -> se actualiza lo que
        traiga relleno. Devuelve (creadas, actualizadas). `key_for(word)` da la clave de la palabra."""
        created = updated = 0
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                word = (row.get("Palabra") or "").strip()
                if not word:
                    continue
                meaning = "\n".join(m.strip() for m in (row.get("Significado") or "").split("|") if m.strip())
                reading = (row.get("Lectura") or "").strip()
                sentence = (row.get("Frase") or "").strip()
                key = key_for(word)
                current = self.by_key(lang, key)
                if current is None:
                    entry = self.add_pending(lang, key, word, sentence, reading)
                    if meaning:
                        self.update(entry.id, meaning=meaning)
                    created += 1
                else:
                    changes = {k: v for k, v in (("meaning", meaning), ("reading", reading)) if v}
                    if changes:
                        self.update(current.id, **changes)
                        updated += 1
        return created, updated

    def close(self):
        self._con.close()


class GlossaryDictionary:
    """Diccionario normal + tu glosario (que gana). Se comporta igual que el diccionario que envuelve."""

    def __init__(self, base, glossary: UserGlossary, lang: str):
        self.base, self.glossary, self.lang = base, glossary, lang

    def __getattr__(self, name):                 # available, build_hint, key_for, has_form, ...
        return getattr(self.base, name)

    def lookup(self, word: str, compound: str = "", full: bool = False):
        key = self.base.key_for(word, compound)
        mine = self.glossary.lookup(self.lang, word, compound, key)
        if mine is not None:
            return mine
        return self.base.lookup(word, compound, full=full) if self.base.available else None
