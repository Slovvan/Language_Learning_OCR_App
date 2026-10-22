"""Registro de frases: cada captura se guarda con su traducción y no se borra al hacer otra.

Base SQLite en data/log.db. Se usa solo desde el hilo de la interfaz.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "log.db"


@dataclass
class LogEntry:
    id: int
    ts: str            # "2026-10-20 09:20:31"
    original: str
    translation: str
    merged: bool       # ¿se unieron las líneas partidas? (decide a qué frase pertenece una palabra)
    score: float       # confianza media del OCR (0 a 1)


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


class PhraseLog:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS captures ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, original TEXT NOT NULL, "
            "translation TEXT NOT NULL DEFAULT '', merged INTEGER NOT NULL DEFAULT 1, "
            "score REAL NOT NULL DEFAULT 0)")
        self._con.commit()

    def __len__(self) -> int:
        return self._con.execute("SELECT COUNT(*) FROM captures").fetchone()[0]

    @staticmethod
    def _entry(row) -> LogEntry:
        return LogEntry(row[0], row[1], row[2], row[3], bool(row[4]), row[5])

    _COLUMNS = "id, ts, original, translation, merged, score"

    def add(self, original: str, translation: str = "", merged: bool = True, score: float = 0.0) -> int:
        """Añade una captura y devuelve su id.

        Si el texto es igual al de la última entrada (volviste a capturar la misma frase),
        no se duplica: se reutiliza esa entrada y se actualiza su hora.
        """
        now = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
        row = self._con.execute(
            f"SELECT {self._COLUMNS} FROM captures ORDER BY id DESC LIMIT 1").fetchone()
        if row is not None and _same(row[2], original):
            keep = translation or row[3]
            self._con.execute("UPDATE captures SET ts=?, translation=?, merged=?, score=? WHERE id=?",
                              (now, keep, int(merged), score, row[0]))
            self._con.commit()
            return row[0]
        cur = self._con.execute(
            "INSERT INTO captures (ts, original, translation, merged, score) VALUES (?,?,?,?,?)",
            (now, original, translation, int(merged), score))
        self._con.commit()
        return cur.lastrowid

    def set_translation(self, entry_id: int, translation: str):
        self._con.execute("UPDATE captures SET translation=? WHERE id=?", (translation, entry_id))
        self._con.commit()

    def get(self, entry_id: int):
        row = self._con.execute(
            f"SELECT {self._COLUMNS} FROM captures WHERE id=?", (entry_id,)).fetchone()
        return self._entry(row) if row else None

    def all(self, query: str = "") -> list:
        """Todas las entradas, la más nueva primero. `query` filtra por original o traducción."""
        rows = self._con.execute(f"SELECT {self._COLUMNS} FROM captures ORDER BY id DESC").fetchall()
        entries = [self._entry(r) for r in rows]
        needle = query.strip().casefold()
        if needle:
            entries = [e for e in entries
                       if needle in e.original.casefold() or needle in e.translation.casefold()]
        return entries

    def delete(self, ids):
        ids = list(ids)
        if ids:
            self._con.executemany("DELETE FROM captures WHERE id=?", [(i,) for i in ids])
            self._con.commit()

    def close(self):
        self._con.close()
