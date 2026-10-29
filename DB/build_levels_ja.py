"""Construye data/levels_ja.db: nivel JLPT y frecuencias (por tipo de medio) para el diccionario japonés.

Uso:
    python build_levels_ja.py                                   # solo JLPT (ya incluido en data/)
    python build_levels_ja.py game=video_game.zip anime=anime.zip vn=visual_novel.zip drama=drama.zip

Las frecuencias son listas de jiten.moe (https://jiten.moe/frequency-dictionaries): en cada fila
de esa página baja la versión «Yomitan» (un .zip, NO lo descomprimas) o la «CSV». El nombre antes
del «=» es la etiqueta: game (videojuegos), anime, vn (novelas visuales), drama, news (noticias),
global (todas). Puedes pasar las que quieras; se pueden añadir más tarde repitiendo el comando con
todas (la base se rehace entera). Cada archivo puede ser .zip de Yomitan o .csv.

Datos: nivel JLPT de Jonathan Waller (tanos.co.uk, CC BY) en la versión de Bluskyo; frecuencias de
jiten.moe (CC BY-SA 4.0), que usa datos de JMdict/EDRDG.
"""
import argparse
import csv
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
DB_PATH = DATA / "levels_ja.db"
JLPT_JSON = DATA / "jlpt_vocab_ALL.json"
KINDS = ("game", "anime", "vn", "drama", "news", "global")

SCHEMA = """
CREATE TABLE info (word TEXT NOT NULL, reading TEXT NOT NULL, kind TEXT NOT NULL, value INTEGER NOT NULL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX info_word ON info(word);
"""


# ---------------------------------------------------------------------------- lectura de listas
def _number(x):
    """Número de un campo de Yomitan (número, {'value': n} o {'frequency': ...})."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, dict):
        if "value" in x:
            return _number(x["value"])
        if "frequency" in x:
            return _number(x["frequency"])
    return None


def parse_yomitan(data: bytes) -> list:
    """[(palabra, lectura, puesto)] de un .zip de frecuencias de Yomitan."""
    rows, mode = [], "rank-based"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if "index.json" in z.namelist():
            mode = json.loads(z.read("index.json").decode("utf-8")).get("frequencyMode", mode)
        for name in sorted(z.namelist()):
            base = name.rsplit("/", 1)[-1]
            if not (base.startswith("term_meta_bank") and base.endswith(".json")):
                continue
            for item in json.loads(z.read(name).decode("utf-8")):
                if len(item) < 3 or item[1] != "freq":
                    continue
                term, payload = item[0], item[2]
                reading = payload.get("reading", "") if isinstance(payload, dict) else ""
                value = _number(payload)
                if value is not None:
                    rows.append((term, reading, value))
    return _to_ranks(rows, mode == "occurrence-based")


def _to_ranks(rows: list, occurrences: bool) -> list:
    """Si los valores son «veces que aparece», se convierten en puesto (1 = la más frecuente)."""
    if not occurrences:
        return [(w, r, int(v)) for w, r, v in rows if v >= 1]
    ordered = sorted(rows, key=lambda x: -x[2])
    return [(w, r, i) for i, (w, r, _v) in enumerate(ordered, 1)]


_WORD_COLS = ("word", "term", "expression", "lemma", "vocab", "vocabulary", "単語", "表記", "語")
_READ_COLS = ("reading", "kana", "yomi", "読み", "読み方")
_RANK_COLS = ("rank", "frequency_rank", "freq_rank", "ranking", "order", "#", "順位")
_COUNT_COLS = ("count", "occurrences", "occurrence", "times", "frequency", "freq", "appearances")


def parse_csv(data: bytes) -> list:
    """[(palabra, lectura, puesto)] de un CSV. Reconoce la cabecera por nombres (word/term,
    reading, rank o count); sin cabecera usa columnas 1 = palabra, 2 = lectura, 3 = puesto."""
    text = data.decode("utf-8-sig")
    dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t") if text.strip() else csv.excel
    table = [r for r in csv.reader(io.StringIO(text), dialect) if r]
    if not table:
        return []
    head = [c.strip().lower() for c in table[0]]

    def col(names):
        return next((head.index(n) for n in names if n in head), None)

    wi, ri, ki, ci = col(_WORD_COLS), col(_READ_COLS), col(_RANK_COLS), col(_COUNT_COLS)
    if wi is None:                                    # sin cabecera conocida: posiciones
        if table[0] and table[0][-1].strip().isdigit():
            wi, ri, ki, body = 0, (1 if len(table[0]) > 2 else None), len(table[0]) - 1, table
        else:
            raise ValueError("No reconozco las columnas del CSV (esperaba word/term, reading y rank).")
    else:
        body = table[1:]
    use_rank = ki is not None
    vi = ki if use_rank else ci
    if vi is None:                                    # solo palabra + lectura: el orden de las filas es el puesto
        rows = [(r[wi].strip(), r[ri].strip() if ri is not None and ri < len(r) else "", n)
                for n, r in enumerate(body, 1) if wi < len(r)]
        return [x for x in rows if x[0]]
    rows = []
    for r in body:
        if wi >= len(r) or vi >= len(r):
            continue
        try:
            value = float(r[vi].replace(",", ""))
        except ValueError:
            continue
        reading = r[ri].strip() if ri is not None and ri < len(r) else ""
        if r[wi].strip():
            rows.append((r[wi].strip(), reading, value))
    return _to_ranks(rows, not use_rank)


def parse_file(path: Path) -> list:
    data = path.read_bytes()
    if zipfile.is_zipfile(path):
        return parse_yomitan(data)
    return parse_csv(data)


def parse_jlpt(path: Path) -> list:
    """[(palabra, lectura, nivel)] del JSON de Bluskyo (palabra -> [{reading, level}])."""
    out = []
    for word, items in json.loads(path.read_text(encoding="utf-8")).items():
        for it in items:
            out.append((word, it.get("reading", ""), int(it["level"])))
    return out


# ---------------------------------------------------------------------------- construcción
def build(sources: dict, jlpt_path: Path, out: Path) -> dict:
    DATA.mkdir(exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    counts = {}
    if jlpt_path.exists():
        rows = parse_jlpt(jlpt_path)
        con.executemany("INSERT INTO info VALUES (?,?,'jlpt',?)", rows)
        counts["jlpt"] = len(rows)
    for kind, path in sources.items():
        rows = parse_file(path)
        # una palabra con la misma lectura solo una vez por fuente (el mejor puesto)
        best = {}
        for word, reading, rank in rows:
            k = (word, reading)
            if k not in best or rank < best[k]:
                best[k] = rank
        con.executemany("INSERT INTO info VALUES (?,?,?,?)", [(w, r, kind, v) for (w, r), v in best.items()])
        counts[kind] = len(best)
    con.execute("INSERT INTO meta VALUES ('sources', ?)", (json.dumps(counts),))
    con.commit()
    con.execute("VACUUM")
    con.close()
    out.unlink(missing_ok=True)
    tmp.rename(out)
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lists", nargs="*", help="etiqueta=archivo  (game, anime, vn, drama, news, global)")
    ap.add_argument("--jlpt", default=str(JLPT_JSON), help="JSON de niveles JLPT")
    ap.add_argument("--out", default=str(DB_PATH))
    args = ap.parse_args()
    sources = {}
    for item in args.lists:
        kind, _, path = item.partition("=")
        if kind not in KINDS or not path:
            sys.exit(f"«{item}»: usa etiqueta=archivo con etiqueta en {', '.join(KINDS)}.")
        if not Path(path).exists():
            sys.exit(f"No existe el archivo: {path}")
        sources[kind] = Path(path)
    try:
        counts = build(sources, Path(args.jlpt), Path(args.out))
    except (ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        sys.exit(f"No se pudo leer una de las listas: {exc}")
    print("Listo -> " + args.out)
    for kind, n in counts.items():
        print(f"  {kind}: {n} palabras")


if __name__ == "__main__":
    main()
