"""Construye el diccionario francés → español (SQLite).

Se ejecuta UNA sola vez:

    python build_dictionary_fr.py

Fuentes (se combinan):
  1. FreeDict + WikDict fra-spa (Wiktionary): palabra, pronunciación (IPA), categoría y género,
     equivalentes en español y definición en francés. Es la fuente principal.
     Hacen falta fra-spa.index y fra-spa.dict.dz (de https://freedict.org/downloads/, formato
     «dictd»); se buscan en data/fr_source/, data/ o junto a este script.
  2. WordNet 3.0 + OMW (WOLF francés, MCR español): solo rellena palabras que FreeDict no tiene
     (descarga ~15 MB; si no hay internet se omite). Sus significados llevan definición en inglés.

Genera data/dictionary_fr.db. Licencias: FreeDict/WikDict/Wiktionary CC BY-SA 3.0; WordNet 3.0
(Princeton, permisiva); WOLF CeCILL-C; MCR CC BY 3.0. Ver data/CREDITS_fr.txt.
"""
import gzip
import io
import re
import sqlite3
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
SRC_DIR = DATA_DIR / "fr_source"
DB_PATH = DATA_DIR / "dictionary_fr.db"
BASE = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora/"
ZIPS = {"wordnet": BASE + "wordnet.zip", "omw-1.4": BASE + "omw-1.4.zip"}

MAX_SYNONYMS = 8
MAX_SPANISH = 8
POS = {"n": "n", "v": "v", "a": "a", "s": "a", "r": "r"}
DATA_FILES = ("data.noun", "data.verb", "data.adj", "data.adv")

SCHEMA = """
CREATE TABLE senses (
    id         INTEGER PRIMARY KEY,
    lemma      TEXT NOT NULL,              -- francés en minúsculas (clave de búsqueda)
    head       TEXT NOT NULL,              -- como se escribe de verdad («Paris»)
    pos        TEXT NOT NULL,              -- etiqueta lista para mostrar: «sust. m.», «verbo», «adj.»…
    ipa        TEXT NOT NULL DEFAULT '',   -- pronunciación: /ʃval/
    rank       INTEGER NOT NULL,           -- 0 = significado más común de ese lema
    definition TEXT NOT NULL DEFAULT '',   -- definición (francesa en FreeDict, inglesa en WordNet)
    example    TEXT NOT NULL DEFAULT '',
    synonyms   TEXT NOT NULL DEFAULT '',   -- separados por |
    spanish    TEXT NOT NULL DEFAULT '',   -- equivalentes en español, separados por |
    src        TEXT NOT NULL DEFAULT ''    -- 'fd' (FreeDict) o 'wn' (WordNet)
);
CREATE TABLE plain (                       -- lema sin tildes -> lemas (búsqueda tolerante a OCR)
    folded TEXT NOT NULL,
    lemma  TEXT NOT NULL,
    PRIMARY KEY (folded, lemma)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
POS_LABELS = {"n": "sust.", "v": "verbo", "adj": "adj.", "adv": "adv.", "pn": "nombre propio", "prep": "prep.",
              "conj": "conj.", "pron": "pron.", "det": "det.", "interj": "interj.", "num": "num.",
              "art": "art.", "intj": "interj.", "prefix": "prefijo", "suffix": "sufijo", "phrase": "locución",
              "abbr": "abrev.", "contraction": "contracción", "particle": "partícula"}
_HEADER = re.compile(r"^(?P<head>.+?)\s*(?P<ipa>(?:/[^/]+/\s*)*)(?:<(?P<tag>[^>]*)>)?\s*$")
_GROUP = re.compile(r"^(\d+)\.\s+(.*)$")
_MARK = re.compile(r"^\s*\d+\.\s*$")
_TRAIL_NUM = re.compile(r"\s+\d+\.\s*$")


def _b64(text: str) -> int:
    n = 0
    for c in text:
        n = n * 64 + _B64.index(c)
    return n


def find_freedict():
    """(index, dict.dz) buscando en data/fr_source, data y junto al script."""
    for folder in (SRC_DIR, DATA_DIR, HERE, SRC_DIR / "freedict"):
        idx, dz = folder / "fra-spa.index", folder / "fra-spa.dict.dz"
        if idx.exists() and dz.exists():
            return idx, dz
    return None


def parse_block(text: str):
    """Un artículo de FreeDict: [(ipa, etiqueta, [(traducciones, definición)])] por bloque de categoría."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip("\n")):
        lines = block.split("\n")
        m = _HEADER.match(lines[0].strip())
        if not m:
            continue
        ipas = re.findall(r"/[^/]+/", m.group("ipa") or "")
        ipa = ipas[0] if ipas else ""
        tag = [t.strip() for t in (m.group("tag") or "").split(",") if t.strip()]
        pos = tag[0] if tag else ""
        label = POS_LABELS.get(pos, pos) or "expr."
        gender = tag[1] if len(tag) > 1 else ""
        if pos == "n" and gender in ("masc", "fem"):
            label = "sust. m." if gender == "masc" else "sust. f."
        groups, cur, fresh = [], None, True        # cur = [traducciones, [definiciones]]
        for ln in lines[1:]:
            if not ln.strip():
                continue
            g = _GROUP.match(ln)
            if g:
                if cur:
                    groups.append(cur)
                cur, fresh = [_TRAIL_NUM.sub("", g.group(2)).strip(), []], True
            elif cur is None:
                cur, fresh = [_TRAIL_NUM.sub("", ln).strip(), []], True
            elif _MARK.match(ln):
                fresh = True                       # otro significado que comparte las traducciones
            elif fresh or not cur[1]:
                cur[1].append(ln.strip())
                fresh = False
            else:
                cur[1][-1] = (cur[1][-1] + " " + ln.strip()).strip()
        if cur:
            groups.append(cur)
        senses = []
        for trans, defs in groups:
            for d in (defs or [""]):
                senses.append((trans, d))
        out.append((ipa, label, senses))
    return out


def read_freedict(idx_path, dz_path) -> dict:
    """{lema en minúsculas: [(head, ipa, etiqueta, traducciones, definición), ...]} en el orden del archivo."""
    import gzip
    data = gzip.open(dz_path, "rb").read()
    out = defaultdict(list)
    for line in open(idx_path, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 3 or parts[0].startswith("00database"):
            continue
        head, off, ln = parts
        text = data[_b64(off):_b64(off) + _b64(ln)].decode("utf-8", "replace")
        for ipa, label, senses in parse_block(text):
            for trans, definition in senses:
                if trans:
                    out[head.lower()].append((text.split(" /")[0].split(" <")[0].split("\n")[0].strip() or head,
                                               ipa, label, trans, definition))
    return out


def fold(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).replace("œ", "oe").replace("æ", "ae")


def download(name: str) -> Path:
    target = SRC_DIR / name
    if target.exists():
        return target
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Descargando {name}...")
    try:
        with urllib.request.urlopen(ZIPS[name], timeout=120) as r:
            blob = r.read()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"No se pudo descargar {name}: {exc}\nRevisa tu conexión a internet.")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        z.extractall(SRC_DIR)
    return target


def read_tab(path: Path, lang_col_ok) -> dict:
    """{offset-pos: [lema, ...]} de un wn-data-*.tab de OMW (sin comentarios, solo ':lemma')."""
    out = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and lang_col_ok(parts[1]):
                if parts[2] not in out[parts[0]]:
                    out[parts[0]].append(parts[2])
    return out


def read_glosses(wordnet: Path) -> dict:
    """{offset-pos: (definición, ejemplo, [lemas ingleses])} desde data.noun / verb / adj / adv."""
    out = {}
    for fname in DATA_FILES:
        with open(wordnet / fname, encoding="utf-8") as f:
            for line in f:
                if line.startswith("  "):
                    continue
                head, _, gloss = line.partition(" | ")
                parts = head.split()
                offset, ss_type, count = parts[0], parts[2], int(parts[3], 16)
                words = [parts[4 + 2 * i].replace("_", " ").lower() for i in range(count)]
                pieces = [p.strip() for p in gloss.split(";")]
                examples = [p.strip(' "') for p in pieces if p.startswith('"')]
                definition = "; ".join(p for p in pieces if not p.startswith('"')).strip()
                key = f"{offset}-{'a' if ss_type == 's' else ss_type}"
                out[key] = (definition, examples[0] if examples else "", words)
    return out


def read_counts(wordnet: Path) -> dict:
    """{offset-pos: veces visto en SemCor} (sirve para ordenar: lo más común primero)."""
    out = defaultdict(int)
    with open(wordnet / "index.sense", encoding="utf-8") as f:
        for line in f:
            sense_key, offset, _num, cnt = line.split()
            ss = sense_key.split("%")[1][0]
            ss = {"1": "n", "2": "v", "3": "a", "4": "r", "5": "a"}[ss]
            key = f"{offset}-{ss}"
            out[key] = max(out[key], int(cnt))       # el sentido más visto de sus lemas, no la suma
    return out


def _split_trans(text: str) -> list:
    return [t.strip() for t in text.split(",") if t.strip()]


def build():
    t0 = time.time()
    found = find_freedict()
    if not found:
        sys.exit("No encuentro fra-spa.index y fra-spa.dict.dz.\n"
                 "Descárgalos de https://freedict.org/downloads/ (francés → español, formato dictd) y "
                 f"ponlos en {SRC_DIR}")
    fd = read_freedict(*found)
    print(f"FreeDict: {len(fd):,} palabras ({time.time() - t0:.0f} s)")

    rows, plain = [], set()
    for lemma, items in fd.items():
        if len(lemma) > 60:
            continue
        verb_like = lemma.endswith(("er", "ir", "re", "oir"))
        order = sorted(range(len(items)), key=lambda i: (
            items[i][2] == "nombre propio" or items[i][0] != items[i][0].lower(),   # nombres propios al final
            not (verb_like and items[i][2] == "verbo"), i))            # «être»: el verbo antes que el sustantivo
        for rank, i in enumerate(order):
            head, ipa, label, trans, definition = items[i]
            rows.append((lemma, head, label, ipa, rank, definition, "", "", "|".join(_split_trans(trans)[:MAX_SPANISH]), "fd"))
        plain.add((fold(lemma), lemma))

    # WordNet/OMW: solo para palabras que FreeDict no tiene
    try:
        wordnet, omw = download("wordnet"), download("omw-1.4")
        fra = read_tab(omw / "fra" / "wn-data-fra.tab", lambda c: c.startswith("fra"))
        spa = read_tab(omw / "mcr" / "wn-data-spa.tab", lambda c: c == "lemma")
        glosses, counts = read_glosses(wordnet), read_counts(wordnet)
        by_lemma = defaultdict(list)
        for key, lemmas in fra.items():
            if key in glosses and key in spa:                           # solo con español
                for lem in lemmas:
                    lem = lem.replace("_", " ").lower()
                    if lem not in fd:
                        by_lemma[lem].append(key)
        labels = {"n": "sust.", "v": "verbo", "a": "adj.", "r": "adv."}
        added = 0
        for lemma, keys in by_lemma.items():
            keys = sorted(set(keys), key=lambda k: (-counts.get(k, 0), k))
            plain.add((fold(lemma), lemma))
            for rank, key in enumerate(keys):
                definition, example, _en = glosses[key]
                syn = [x.replace("_", " ") for x in fra[key] if x.replace("_", " ").lower() != lemma]
                es = [x.replace("_", " ") for x in spa.get(key, [])]
                rows.append((lemma, lemma, labels[POS[key[-1]]], "", rank, definition, example,
                             "|".join(syn[:MAX_SYNONYMS]), "|".join(es[:MAX_SPANISH]), "wn"))
                added += 1
        print(f"WordNet: {len(by_lemma):,} palabras más que FreeDict no tenía ({added:,} significados)")
    except SystemExit:
        print("[aviso] Sin WordNet (no hay internet): el diccionario usa solo FreeDict.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO senses (lemma, head, pos, ipa, rank, definition, example, synonyms, spanish, src) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT OR IGNORE INTO plain VALUES (?,?)", plain)
    con.execute("CREATE INDEX idx_senses_lemma ON senses(lemma, rank)")
    con.execute("CREATE INDEX idx_plain ON plain(folded)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("source", "FreeDict/WikDict fra-spa (Wiktionary) + WordNet/OMW"),
        ("built_at", time.strftime("%Y-%m-%d %H:%M:%S")),
        ("senses", str(len(rows))), ("lemmas", str(len({r[0] for r in rows})))])
    con.commit()
    con.close()
    tmp.replace(DB_PATH)
    print(f"Listo: {len({r[0] for r in rows}):,} palabras, {len(rows):,} significados, "
          f"{DB_PATH.stat().st_size / 1e6:.0f} MB, {time.time() - t0:.0f} s -> {DB_PATH}")


if __name__ == "__main__":
    build()
