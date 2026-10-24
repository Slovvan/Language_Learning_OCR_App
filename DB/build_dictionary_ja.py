"""Construye data/dictionary_ja.db a partir de JMdict (versión «jmdict-simplified», glosas en inglés).

Uso (una sola vez; tarda uno o dos minutos y necesita ~500 MB de RAM):
    python build_dictionary_ja.py                 # descarga la última versión desde GitHub
    python build_dictionary_ja.py --file jmdict-eng-3.6.1.json    # usa un JSON ya descargado

JMdict es © Electronic Dictionary Research and Development Group (EDRDG), licencia CC BY-SA 4.0:
https://www.edrdg.org/edrdg/licence.html  (cítalo si compartes los datos).
"""
import argparse
import io
import json
import re
import sqlite3
import sys
import urllib.request
import zipfile
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
DB_PATH = DATA / "dictionary_ja.db"
API = "https://api.github.com/repos/scriptin/jmdict-simplified/releases/latest"
FALLBACK = ("https://github.com/scriptin/jmdict-simplified/releases/download/"
            "3.6.1%2B20250818123231/jmdict-eng-3.6.1+20250818123231.json.zip")

SCHEMA = """
CREATE TABLE entry (
    id      INTEGER PRIMARY KEY,   -- id de JMdict
    common  INTEGER NOT NULL,      -- 1 si alguna forma es de uso común
    kanji   TEXT NOT NULL,         -- escrituras con kanji separadas por «|» (puede estar vacío)
    kana    TEXT NOT NULL,         -- lecturas separadas por «|»
    pos     TEXT NOT NULL,         -- categorías de todos los significados, separadas por espacio
    nsense  INTEGER NOT NULL
);
CREATE TABLE form (               -- cada texto con el que se puede encontrar una entrada
    text     TEXT NOT NULL,
    entry_id INTEGER NOT NULL,
    reading  TEXT NOT NULL,        -- lectura que corresponde a ese texto
    common   INTEGER NOT NULL
);
CREATE TABLE sense (
    entry_id INTEGER NOT NULL,
    n        INTEGER NOT NULL,
    pos      TEXT NOT NULL,        -- categorías separadas por espacio
    gloss    TEXT NOT NULL,        -- significados separados por «|»
    misc     TEXT NOT NULL         -- etiquetas (uk = normalmente en kana, col = coloquial...)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
INDEXES = """
CREATE INDEX form_text ON form(text);
CREATE INDEX sense_entry ON sense(entry_id, n);
"""


def download() -> bytes:
    url = FALLBACK
    try:
        req = urllib.request.Request(API, headers={"User-Agent": "ocr-translator"})
        with urllib.request.urlopen(req, timeout=30) as r:
            release = json.load(r)
        for asset in release.get("assets", []):
            name = asset["name"]
            if name.startswith("jmdict-eng-") and name.endswith(".json.zip") and "common" not in name:
                url = asset["browser_download_url"]
                break
    except Exception as exc:  # noqa: BLE001
        print(f"[aviso] no se pudo consultar la última versión ({exc}); uso la fija.")
    print(f"Descargando {url} ...")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ocr-translator"}),
                                timeout=120) as r:
        return r.read()


def load_text(args) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    with zipfile.ZipFile(io.BytesIO(download())) as z:
        name = next(n for n in z.namelist() if n.endswith(".json"))
        return z.read(name).decode("utf-8")


def iter_words(text: str):
    """Recorre la lista «words» del JSON de una en una (sin construir todo el árbol de Python)."""
    dec = json.JSONDecoder()
    i = text.index('"words"')
    i = text.index("[", i) + 1
    n = len(text)
    while True:
        while text[i] in " \n\r\t,":
            i += 1
        if text[i] == "]":
            return
        word, i = dec.raw_decode(text, i)
        yield word


def applies(applies_to: list, key: str) -> bool:
    return "*" in applies_to or key in applies_to


def build(text: str, out: Path):
    m = re.search(r'"version"\s*:\s*"([^"]+)"', text[:2000])
    version = m.group(1) if m else "?"
    DATA.mkdir(exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    entries, forms, senses = [], [], []
    count = 0
    for w in iter_words(text):
        count += 1
        kanji = [k for k in w["kanji"]]
        kana = [k for k in w["kana"]]
        common = int(any(f["common"] for f in kanji + kana))
        sense_rows, pos_all = [], []
        for n, s in enumerate(w["sense"]):
            glosses = [g["text"] for g in s["gloss"] if g.get("lang", "eng") == "eng"]
            if not glosses:
                continue
            pos = " ".join(s["partOfSpeech"])
            pos_all.extend(s["partOfSpeech"])
            sense_rows.append((w["id"], len(sense_rows), pos, "|".join(glosses), " ".join(s["misc"])))
        if not sense_rows:
            continue
        entries.append((w["id"], common, "|".join(k["text"] for k in kanji),
                        "|".join(k["text"] for k in kana), " ".join(dict.fromkeys(pos_all)),
                        len(sense_rows)))
        senses.extend(sense_rows)
        for k in kanji:
            if "sK" in k["tags"] or "iK" in k["tags"]:        # formas «solo kanji raro» o incorrectas
                continue
            readings = [r for r in kana if applies(r.get("appliesToKanji", ["*"]), k["text"])] or kana
            readings.sort(key=lambda r: not r["common"])
            forms.append((k["text"], w["id"], readings[0]["text"], int(k["common"])))
        for r in kana:
            if "sk" in r["tags"] or "ik" in r["tags"]:
                continue
            forms.append((r["text"], w["id"], r["text"], int(r["common"])))
        if count % 40000 == 0:
            print(f"  {count} entradas...")
    con.executemany("INSERT INTO entry VALUES (?,?,?,?,?,?)", entries)
    con.executemany("INSERT INTO form VALUES (?,?,?,?)", forms)
    con.executemany("INSERT INTO sense VALUES (?,?,?,?,?)", senses)
    con.executemany("INSERT INTO meta VALUES (?,?)",
                    [("source", "JMdict (jmdict-simplified, eng)"), ("version", str(version)),
                     ("licence", "CC BY-SA 4.0 - EDRDG")])
    con.executescript(INDEXES)
    con.commit()
    con.execute("VACUUM")
    con.close()
    out.unlink(missing_ok=True)
    tmp.rename(out)
    print(f"Listo: {len(entries)} entradas, {len(forms)} formas, {len(senses)} significados "
          f"-> {out} ({out.stat().st_size / 1e6:.0f} MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="JSON de jmdict-simplified ya descargado (sin comprimir)")
    ap.add_argument("--out", default=str(DB_PATH))
    args = ap.parse_args()
    try:
        text = load_text(args)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"No se pudo obtener JMdict: {exc}\nDescarga jmdict-eng-*.json.zip de "
                 "https://github.com/scriptin/jmdict-simplified/releases, descomprímelo y usa --file.")
    build(text, Path(args.out))


if __name__ == "__main__":
    main()
