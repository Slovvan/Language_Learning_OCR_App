"""Tarjetas de vocabulario: datos, base SQLite y construcción a partir de un clic.

Una tarjeta guarda la palabra, su significado, la frase donde apareció y la traducción de esa
frase (y, en japonés, su lectura). Hay una sola tarjeta por palabra y por idioma: la clave es su
lema principal (ver Dictionary.key_for), así que «sword» y «swords» son la misma tarjeta.
Cada idioma tiene su propia lista: el almacén trabaja con el idioma activo (`set_language`). La base está en data/cards.db, separada del
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
EDITABLE = frozenset({"word", "reading", "meaning", "extra", "sentence", "sentence_es"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,             -- lema principal: evita duplicados (dentro de cada idioma)
    lang        TEXT NOT NULL DEFAULT 'en',  -- idioma del juego: en, ja
    word        TEXT NOT NULL,             -- lo que se ve en el anverso
    surface     TEXT NOT NULL,             -- la palabra tal como apareció en la frase
    reading     TEXT NOT NULL DEFAULT '',  -- lectura en kana (japonés)
    meaning     TEXT NOT NULL DEFAULT '',  -- una línea por significado
    extra       TEXT NOT NULL DEFAULT '',  -- sinónimos y antónimos
    sentence    TEXT NOT NULL DEFAULT '',
    sentence_es TEXT NOT NULL DEFAULT '',  -- traducción de la frase (al idioma de destino)
    created_at  TEXT NOT NULL,
    UNIQUE (lang, key)
)
"""
_COLUMNS = "id, key, lang, word, surface, reading, meaning, extra, sentence, sentence_es, created_at"


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
    lang: str = ""          # idioma del juego; vacío = el idioma activo del almacén al guardar
    reading: str = ""       # lectura en kana (solo japonés)


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


def build_card(word: str, key: str, result, sentence: str, lang: str = "") -> Card:
    """Tarjeta para `word` (la palabra del texto) con su significado y su frase.

    `result` es el LookupResult del diccionario, o None si la palabra no tiene entrada
    (p. ej. «mana»): en ese caso el significado queda vacío hasta que llegue la
    traducción automática de la palabra.
    """
    if result is None:
        return Card(key=key, word=word, surface=word, sentence=sentence, lang=lang)
    reading = next((e.reading for e in result.entries if e.reading), "")
    return Card(key=key, word=key, surface=result.query, meaning=meaning_text(result),
                extra=extra_text(result), sentence=sentence, lang=lang, reading=reading)


class CardStore:
    """Base de datos de tarjetas. Úsala solo desde el hilo de la interfaz."""

    def __init__(self, path: Path = DB_PATH, lang: str = "en"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._migrate()
        self._con.execute(_SCHEMA)
        self._con.commit()
        self.lang = lang
        self._keys = self._load_keys()

    def _migrate(self):
        """Bases de las fases anteriores: sin columna de idioma y con la clave única a secas.
        Se reconstruye la tabla (todas las tarjetas viejas pasan a inglés) sin perder nada."""
        cols = [r[1] for r in self._con.execute("PRAGMA table_info(cards)")]
        if not cols or "lang" in cols:
            return
        self._con.execute("ALTER TABLE cards RENAME TO cards_old")
        self._con.execute(_SCHEMA)
        self._con.execute(
            "INSERT INTO cards (id, key, lang, word, surface, reading, meaning, extra, sentence, "
            "sentence_es, created_at) SELECT id, key, 'en', word, surface, '', meaning, extra, "
            "sentence, sentence_es, created_at FROM cards_old")
        self._con.execute("DROP TABLE cards_old")
        self._con.commit()

    def _load_keys(self) -> set:
        return {row[0] for row in self._con.execute("SELECT key FROM cards WHERE lang=?", (self.lang,))}

    def set_language(self, lang: str):
        """Cambia el idioma con el que trabaja el almacén (claves, conteo, lista y nuevas tarjetas)."""
        self.lang = lang
        self._keys = self._load_keys()

    # --- consulta ---
    @property
    def keys(self) -> set:
        """Claves de las tarjetas guardadas en el idioma activo (no la modifiques)."""
        return self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def has(self, key: str) -> bool:
        return key in self._keys

    def count(self, lang: str) -> int:
        return self._con.execute("SELECT COUNT(*) FROM cards WHERE lang=?", (lang,)).fetchone()[0]

    def get(self, card_id: int) -> Optional[Card]:
        row = self._con.execute(f"SELECT {_COLUMNS} FROM cards WHERE id=?", (card_id,)).fetchone()
        return self._card(row) if row else None

    def all(self, lang: Optional[str] = None) -> list:
        """Tarjetas del idioma `lang` (por defecto el activo), de la más vieja a la más nueva."""
        rows = self._con.execute(f"SELECT {_COLUMNS} FROM cards WHERE lang=? ORDER BY id",
                                 (lang or self.lang,)).fetchall()
        return [self._card(r) for r in rows]

    # --- cambios ---
    def add(self, card: Card) -> Optional[Card]:
        """Guarda la tarjeta en su idioma (por defecto, el activo). Devuelve None, sin tocar nada,
        si ya existía esa clave en ese idioma."""
        card.lang = card.lang or self.lang
        if self._con.execute("SELECT 1 FROM cards WHERE lang=? AND key=?", (card.lang, card.key)).fetchone():
            return None
        card.created_at = card.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = self._con.execute(
            "INSERT INTO cards (key, lang, word, surface, reading, meaning, extra, sentence, "
            "sentence_es, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (card.key, card.lang, card.word, card.surface, card.reading, card.meaning, card.extra,
             card.sentence, card.sentence_es, card.created_at))
        self._con.commit()
        card.id = cur.lastrowid
        if card.lang == self.lang:
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
        self._keys = self._load_keys()

    def close(self):
        self._con.close()

    @staticmethod
    def _card(row) -> Card:
        return Card(id=row[0], key=row[1], lang=row[2], word=row[3], surface=row[4], reading=row[5],
                    meaning=row[6], extra=row[7], sentence=row[8], sentence_es=row[9], created_at=row[10])
