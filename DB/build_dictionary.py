"""Construye el diccionario local (SQLite) a partir de WordNet 3.0 + Open Multilingual WordNet.

Se ejecuta UNA sola vez:

    pip install nltk
    python build_dictionary.py

- Descarga ~40 MB de datos desde GitHub (WordNet y OMW) a data/nltk_data/.
- Tarda de 1 a 3 minutos y genera data/dictionary.db.
- Después NLTK ya no hace falta: dictionary.py solo usa sqlite3 (librería estándar).
- Para cada palabra guarda sus significados (del más común al menos común), sinónimos,
  antónimos y equivalentes en español, más las formas irregulares ("ran" -> "run").

Licencias: WordNet 3.0 (c) Princeton University (licencia permisiva);
español: Multilingual Central Repository vía Open Multilingual WordNet (CC BY 3.0).
"""
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DB_PATH = DATA_DIR / "dictionary.db"
NLTK_DIR = DATA_DIR / "nltk_data"

MAX_SYNONYMS = 10
MAX_SPANISH = 8
# Los adjetivos "satélite" (s) cuentan como adjetivos (a), igual que en el índice de WordNet.
POS_MAP = {"n": "n", "v": "v", "a": "a", "s": "a", "r": "r"}
EXC_FILES = {"n": "noun.exc", "v": "verb.exc", "a": "adj.exc", "r": "adv.exc"}

SCHEMA = """
CREATE TABLE senses (
    id         INTEGER PRIMARY KEY,
    lemma      TEXT NOT NULL,              -- minúsculas, como en WordNet (multipalabra con "_")
    pos        TEXT NOT NULL,              -- n, v, a, r
    rank       INTEGER NOT NULL,           -- 0 = significado más común de ese lema
    definition TEXT NOT NULL,
    example    TEXT NOT NULL DEFAULT '',
    synonyms   TEXT NOT NULL DEFAULT '',   -- separados por |
    antonyms   TEXT NOT NULL DEFAULT '',
    spanish    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE exceptions (
    form  TEXT NOT NULL,                   -- forma irregular: "geese", "ran", "better"
    pos   TEXT NOT NULL,
    lemma TEXT NOT NULL,
    PRIMARY KEY (form, pos, lemma)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _have(resource: str) -> bool:
    import nltk
    try:
        nltk.data.find(resource)
        return True
    except LookupError:
        return False


def ensure_data():
    """Descarga WordNet y OMW si faltan. Devuelve (wordnet, hay_español)."""
    import nltk

    NLTK_DIR.mkdir(parents=True, exist_ok=True)
    nltk.data.path.insert(0, str(NLTK_DIR))

    if not _have("corpora/wordnet"):
        print("Descargando WordNet...")
        if not nltk.download("wordnet", download_dir=str(NLTK_DIR), quiet=True):
            sys.exit("No se pudo descargar WordNet. Revisa tu conexión a internet.")

    from nltk.corpus import wordnet as wn

    probe = wn.synset("dog.n.01")
    # Según la versión de NLTK se necesita omw-2.0 u omw-1.4.
    for pkg in ("omw-2.0", "omw-1.4"):
        if not _have(f"corpora/{pkg}"):
            print(f"Descargando {pkg} (español)...")
            nltk.download(pkg, download_dir=str(NLTK_DIR), quiet=True)
        try:
            if probe.lemma_names("spa"):
                return wn, True
        except LookupError:
            continue
    print("[aviso] No se pudo cargar el español de OMW: el diccionario quedará sin equivalentes en español.")
    return wn, False


def _clean(name: str) -> str:
    return name.replace("_", " ")


def _unique(items, skip=""):
    seen, out = set(), []
    for item in items:
        key = item.lower()
        if key != skip and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def build():
    t0 = time.time()
    wn, has_spanish = ensure_data()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)

    names = sorted({n.lower() for n in wn.all_lemma_names()})
    total = len(names)
    print(f"Procesando {total:,} palabras...")

    insert = ("INSERT INTO senses (lemma, pos, rank, definition, example, synonyms, antonyms, spanish) "
              "VALUES (?,?,?,?,?,?,?,?)")
    rows, n_rows = [], 0
    for i, name in enumerate(names, 1):
        lemmas = wn.lemmas(name)  # solo coincidencias exactas del lema, no formas flexionadas
        # Más común primero (frecuencia en SemCor); a igualdad, el orden original de WordNet.
        ordered = sorted(enumerate(lemmas), key=lambda t: (-t[1].count(), t[0]))
        for rank, (_, lemma) in enumerate(ordered):
            syn = lemma.synset()
            examples = syn.examples()
            synonyms = _unique((_clean(x.name()) for x in syn.lemmas()), skip=_clean(name))
            antonyms = _unique(_clean(a.name()) for a in lemma.antonyms())
            spanish = _unique(_clean(x) for x in syn.lemma_names("spa")) if has_spanish else []
            rows.append((
                name, POS_MAP[syn.pos()], rank, syn.definition(),
                examples[0] if examples else "",
                "|".join(synonyms[:MAX_SYNONYMS]),
                "|".join(antonyms),
                "|".join(spanish[:MAX_SPANISH]),
            ))
        if len(rows) >= 20000:
            con.executemany(insert, rows)
            n_rows += len(rows)
            rows = []
        if i % 20000 == 0:
            print(f"  {i:,}/{total:,}  ({time.time() - t0:.0f} s)")
    con.executemany(insert, rows)
    n_rows += len(rows)

    # Formas irregulares (geese -> goose, ran -> run, better -> good ...)
    exc_rows = set()
    for pos, fname in EXC_FILES.items():
        f = wn.open(fname)
        try:
            for line in f:
                parts = line.split()
                for lem in parts[1:]:
                    exc_rows.add((parts[0].lower(), pos, lem.lower()))
        finally:
            f.close()
    con.executemany("INSERT OR IGNORE INTO exceptions VALUES (?,?,?)", exc_rows)

    print("Creando índices...")
    con.execute("CREATE INDEX idx_senses_lemma_rank ON senses(lemma, rank)")
    con.execute("CREATE INDEX idx_senses_lemma_pos ON senses(lemma, pos)")
    con.execute("CREATE INDEX idx_exceptions_form ON exceptions(form)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("source", "WordNet 3.0 + Open Multilingual WordNet (MCR, español)"),
        ("spanish", "1" if has_spanish else "0"),
        ("built_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("senses", str(n_rows)),
    ])
    con.commit()
    con.close()
    tmp.replace(DB_PATH)  # reemplazo atómico: nunca queda una base a medio construir

    size_mb = DB_PATH.stat().st_size / 1e6
    print(f"Listo: {n_rows:,} significados, {size_mb:.0f} MB, {time.time() - t0:.0f} s -> {DB_PATH}")


if __name__ == "__main__":
    build()