"""Tarjetas de vocabulario: datos, base SQLite y construcción a partir de un clic.

Una tarjeta guarda la palabra, su significado, la frase donde apareció y la traducción de esa
frase. Hay una sola tarjeta por palabra: la clave es su lema principal (ver Dictionary.key_for),
así que «sword» y «swords» son la misma tarjeta. La base está en data/cards.db, separada del
diccionario, para que reconstruir el diccionario no borre tus tarjetas.

Esta parte no usa Qt: se puede probar sola.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from DB.dictionary import POS_LABEL

DB_PATH = Path(__file__).resolve().parent / "data" / "cards.db"

MAX_MEANING_LINES = 3      # significados que se copian a la tarjeta (se pueden editar después)
MAX_DEFINITION = 160       # caracteres de cada definición en inglés
EDITABLE = frozenset({"word", "meaning", "extra", "sentence", "sentence_es"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,      -- lema principal: evita duplicados
    word        TEXT NOT NULL,             -- lo que se ve en el anverso
    surface     TEXT NOT NULL,             -- la palabra tal como apareció en la frase
    meaning     TEXT NOT NULL DEFAULT '',  -- una línea por significado
    extra       TEXT NOT NULL DEFAULT '',  -- sinónimos y antónimos
    sentence    TEXT NOT NULL DEFAULT '',
    sentence_es TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
)
"""
_COLUMNS = "id, key, word, surface, meaning, extra, sentence, sentence_es, created_at"


@dataclass
class Card:
    key: str
    word: str
    surface: str
    meaning: str = ""
    extra: str = ""
    sentence: str = ""
    sentence_es: str = ""
    created_at: str = ""
    id: Optional[int] = None


def _shorten(text: str, limit: int = MAX_DEFINITION) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def meaning_text(result) -> str:
    """Significados de un LookupResult como texto plano, una línea por significado.

    Formato: «(sust.) espada, garrancha — a cutting or thrusting weapon...». Si hay varios
    lemas (p. ej. «running» -> «running» y «run»), los del segundo en adelante llevan su lema.
    """
    lines = []
    for n, entry in enumerate(result.entries):
        for sense in entry.senses:
            if len(lines) >= MAX_MEANING_LINES:
                return "\n".join(lines)
            label = POS_LABEL.get(sense.pos, sense.pos)
            spanish = ", ".join(sense.spanish[:4])
            definition = _shorten(sense.definition)
            body = f"{spanish} — {definition}" if spanish and definition else (spanish or definition)
            prefix = f"{entry.lemma}: " if n > 0 else ""
            lines.append(f"{prefix}({label}) {body}")
    return "\n".join(lines)


def extra_text(result) -> str:
    """Sinónimos y antónimos del primer significado."""
    for entry in result.entries:
        for sense in entry.senses:
            parts = []
            if sense.synonyms:
                parts.append("≈ " + ", ".join(sense.synonyms[:5]))
            if sense.antonyms:
                parts.append("≠ " + ", ".join(sense.antonyms[:4]))
            return "\n".join(parts)
    return ""


def build_card(word: str, key: str, result, sentence: str) -> Card:
    """Tarjeta para `word` (la palabra del texto) con su significado y su frase.

    `result` es el LookupResult del diccionario, o None si la palabra no tiene entrada
    (p. ej. «mana»): en ese caso el significado queda vacío hasta que llegue la
    traducción automática de la palabra.
    """
    if result is None:
        return Card(key=key, word=word, surface=word, sentence=sentence)
    return Card(key=key, word=key, surface=result.query, meaning=meaning_text(result),
                extra=extra_text(result), sentence=sentence)


class CardStore:
    """Base de datos de tarjetas. Úsala solo desde el hilo de la interfaz."""

    def __init__(self, path: Path = DB_PATH):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._con.execute(_SCHEMA)
        self._con.commit()
        self._keys = {row[0] for row in self._con.execute("SELECT key FROM cards")}

    # --- consulta ---
    @property
    def keys(self) -> set:
        """Claves de todas las tarjetas guardadas (no la modifiques)."""
        return self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def has(self, key: str) -> bool:
        return key in self._keys

    def get(self, card_id: int) -> Optional[Card]:
        row = self._con.execute(f"SELECT {_COLUMNS} FROM cards WHERE id=?", (card_id,)).fetchone()
        return self._card(row) if row else None

    def all(self) -> list:
        rows = self._con.execute(f"SELECT {_COLUMNS} FROM cards ORDER BY id").fetchall()
        return [self._card(r) for r in rows]

    # --- cambios ---
    def add(self, card: Card) -> Optional[Card]:
        """Guarda la tarjeta. Devuelve None, sin tocar nada, si ya existía esa clave."""
        if card.key in self._keys:
            return None
        card.created_at = card.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = self._con.execute(
            "INSERT INTO cards (key, word, surface, meaning, extra, sentence, sentence_es, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (card.key, card.word, card.surface, card.meaning, card.extra,
             card.sentence, card.sentence_es, card.created_at))
        self._con.commit()
        card.id = cur.lastrowid
        self._keys.add(card.key)
        return card

    def update(self, card_id: int, **fields):
        bad = set(fields) - EDITABLE
        if bad:
            raise ValueError(f"Campos no editables: {sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{name}=?" for name in fields)
        self._con.execute(f"UPDATE cards SET {sets} WHERE id=?", (*fields.values(), card_id))
        self._con.commit()

    def delete(self, ids):
        ids = list(ids)
        self._con.executemany("DELETE FROM cards WHERE id=?", [(i,) for i in ids])
        self._con.commit()
        self._keys = {row[0] for row in self._con.execute("SELECT key FROM cards")}

    def close(self):
        self._con.close()

    @staticmethod
    def _card(row) -> Card:
        return Card(id=row[0], key=row[1], word=row[2], surface=row[3], meaning=row[4],
                    extra=row[5], sentence=row[6], sentence_es=row[7], created_at=row[8])