"""Posprocesado del OCR en japonés: unir fragmentos, corregir confusiones y tratar el furigana.

    from japanese import process, Mode
    res = process(ocr_result, Mode.NORMAL)      # ocr_result: ocr_engine.OcrResult (con cajas)

Modos:
  NORMAL     el furigana se detecta, se quita del texto y se descarta (frases limpias).
  FURIGANA   igual, pero guarda las lecturas: res.readings = [(inicio, fin, "ゆうしゃ"), ...] con
             posiciones en res.text. Solo se asocia una lectura cuando está claro a qué palabra va.
  KANA       texto en hiragana/katakana: no se busca furigana y las confusiones kana/kanji
             (一→ー, 卜→ト, 口→ロ, 〈→く...) se corrigen sin pedir contexto de katakana a ambos lados.

En todos los modos, los fragmentos que el OCR separa en una misma fila (コ | 一ヒ | を飲みます) se
unen por su posición, de izquierda a derecha, sin espacios. Cada línea sale con su caja.
"""
import re
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum


class Mode(str, Enum):
    NORMAL = "normal"
    FURIGANA = "furigana"
    KANA = "kana"
    VERTICAL = "vertical"      # texto en columnas: cada columna una línea, de derecha a izquierda


RUBY_MAX_RATIO = 0.7        # el furigana mide, como mucho, esta fracción de la altura del texto base
RUBY_MIN_OVERLAP = 0.4      # fracción del ancho del furigana que debe quedar sobre el texto base
RUBY_MAX_GAP = 0.8          # separación vertical máxima (en alturas del texto base)
NOISE_SCORE = 0.85          # una caja pequeña con kanji y menos confianza que esto, sobre una línea, es ruido
COLUMN_OVERLAP = 0.5       # solapamiento horizontal mínimo (fracción de la caja más estrecha) para ser de la misma columna
ROW_OVERLAP = 0.5           # solapamiento vertical mínimo para considerar que dos cajas son de la misma fila
SPACE_GAP = 1.0             # hueco (en alturas de línea) a partir del cual se separan trozos con un espacio
GAP_EDGE = 0.5              # franja de relleno: lo que se sale (en anchos de carácter) en los extremos de la línea
GAP_MIN_SCORE = 0.8         # confianza mínima de una lectura leída en una franja
GAP_MAX_PER_KANJI = 6       # una lectura plausible tiene entre 1 y 6 kana por kanji
READING_REACH = 0.25        # cuánto (en anchos de carácter) puede quedarse corto/largo el furigana para «cubrir» un kanji
READING_OVERHANG = 1.0      # lo que el furigana puede sobresalir del texto que cubre (en anchos de ese texto)
REFINE_ACCEPT = 0.8         # confianza mínima para aceptar la relectura de algo que no parecía furigana (γ2, s47...)
STRAY_SCORE = 0.8           # texto corto sin japonés y con menos confianza que esto es ruido (C, 6, γ2, 1...)
STRAY_MAX_LEN = 2


# ---------------------------------------------------------------------------- caracteres
def is_hira(c: str) -> bool:
    return "ぁ" <= c <= "ゟ"


def is_kata(c: str) -> bool:                  # incluye ー (U+30FC) y ・ (U+30FB)
    return "ァ" <= c <= "ヿ"


def is_kana(c: str) -> bool:
    return is_hira(c) or is_kata(c)


def is_kanji(c: str) -> bool:
    return c != "" and ("\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf" or c in "々〆")


_KANJI_RUN = re.compile("[一-鿿㐀-䶿々〆]+")

# Kanji o signos que el OCR confunde con un kana (→ la forma en katakana).
_KATA_LOOKALIKE = {"一": "ー", "卜": "ト", "口": "ロ", "力": "カ", "夕": "タ", "工": "エ",
                   "二": "ニ", "八": "ハ"}
_HIRA_LOOKALIKE = {"〈": "く", "＜": "く"}
_EDGE = set("、。！？!?…「」『』（）() 　")        # límites de palabra: signos, espacios y fin de línea
_ORPHAN = set("！？!?。…‥、」』）)")                  # signos que no pueden ir solos en una línea
# Cifras y letras de ancho completo -> ASCII (5０ -> 50). Una a una, para no mover posiciones.
_WIDE = {**{chr(0xFF10 + i): chr(0x30 + i) for i in range(10)},
         **{chr(0xFF21 + i): chr(0x41 + i) for i in range(26)},
         **{chr(0xFF41 + i): chr(0x61 + i) for i in range(26)}}
_ANCHOR_EXCLUDE = set("ー・")


def _fix_runs(text: str) -> str:
    """Rachas de katakana con falsos amigos (口一ド -> ロード): si la racha tiene al menos un katakana
    de verdad, los falsos amigos que contiene son katakana. Un falso amigo en el borde de la racha
    pegado a un kanji o hiragana se queda como está (ボール一個, ラーメン一つ, カード二人)."""
    out, n, i = list(text), len(text), 0
    while i < n:
        if is_kata(text[i]) or text[i] in _KATA_LOOKALIKE:
            j = i
            while j < n and (is_kata(text[j]) or text[j] in _KATA_LOOKALIKE):
                j += 1
            run = text[i:j]
            if len(run) >= 2 and any(is_kata(c) and c not in _ANCHOR_EXCLUDE for c in run):
                for k in range(i, j):
                    c = text[k]
                    if c not in _KATA_LOOKALIKE:
                        continue
                    if k == i and i > 0 and not _kata_or_edge(text[i - 1]):
                        continue                         # 一 tras kanji/hiragana: 一口, 買い一括
                    if k == j - 1 and j < n and not _kata_or_edge(text[j]):
                        continue                         # 一 antes de kanji/hiragana: ボール一個, ラーメン一つ
                    out[k] = _KATA_LOOKALIKE[c]
            i = j
        else:
            i += 1
    return "".join(out)


def _attach_orphans(lines: list) -> list:
    """Una línea que solo trae signos de cierre («！» suelto bajo «敵を倒した») se pega a la anterior."""
    out = []
    for line in lines:
        t = line.text.strip()
        if out and t and all(c in _ORPHAN for c in t):
            prev = out[-1]
            start = len(prev.text)
            prev.text += t
            prev.segments = prev.segments + [(start, start + len(t), line.box)]
            prev.box = (min(prev.box[0], line.box[0]), min(prev.box[1], line.box[1]),
                        max(prev.box[2], line.box[2]), max(prev.box[3], line.box[3]))
            continue
        out.append(line)
    return out


def _kata_or_edge(c: str) -> bool:
    return c == "" or is_kata(c) or c in _EDGE


def fix_chars(text: str, mode: Mode) -> str:
    """Corrige confusiones kana/kanji según el contexto. Cambia caracteres uno a uno: no mueve posiciones.

    NORMAL/FURIGANA (hay kanji de verdad, así que se pide contexto de katakana):
      〈→く junto a hiragana · 卜→ト junto a katakana · 一→ー entre katakana (o tras katakana al final)
      · 口 力 夕 工 二 八 → ロ カ タ エ ニ ハ con katakana a un lado y katakana o límite al otro.
    Todos los modos: ? y ! tras texto japonés pasan a ？ y ！.
    KANA (todo el texto es kana): basta con un vecino kana y ninguno kanji; entre hiragana solo 一→ー.
    """
    n = len(text)
    if mode is not Mode.KANA:
        text = _fix_runs(text)
    out = [_WIDE.get(c, c) for c in text]
    for i, c in enumerate(text):
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < n else ""
        if c in "?!" and (is_kana(prev) or is_kanji(prev) or prev in "、。」』）…"):
            out[i] = "？" if c == "?" else "！"                   # el OCR da ? y ! de ancho medio tras japonés
        elif c in _HIRA_LOOKALIKE:
            if is_hira(prev) or is_hira(nxt):
                out[i] = _HIRA_LOOKALIKE[c]
        elif c in _KATA_LOOKALIKE:
            if mode is Mode.KANA:
                if is_kanji(prev) or is_kanji(nxt):
                    continue
                ok = is_kata(prev) or is_kata(nxt) or (c == "一" and (is_hira(prev) or is_hira(nxt)))
            elif c == "卜":
                ok = is_kata(prev) or is_kata(nxt)
            elif c == "一":
                ok = is_kata(prev) and _kata_or_edge(nxt)
            else:
                ok = (is_kata(prev) or is_kata(nxt)) and _kata_or_edge(prev) and _kata_or_edge(nxt)
            if ok:
                out[i] = _KATA_LOOKALIKE[c]
    return "".join(out)


_SMALL = dict(zip("ぁぃぅぇぉゃゅょゎっァィゥェォャュョヮッ", "あいうえおやゆよわつアイウエオヤユヨワツ"))


def clean_reading(text: str) -> str:
    """Una lectura no puede empezar por un kana pequeño (ゃ ゅ ょ ぁ っ...): el OCR lo confunde (ょる → よる)."""
    text = text.strip()
    return _SMALL.get(text[:1], text[:1]) + text[1:] if text else text


def _weight(c: str) -> float:
    """Ancho relativo de un carácter: los de ancho completo valen 1; latinos y dígitos, medio."""
    return 1.0 if unicodedata.east_asian_width(c) in "WF" else 0.55


# ---------------------------------------------------------------------------- piezas del OCR
@dataclass
class _Item:
    text: str
    score: float
    box: tuple                      # (x0, y0, x1, y1)

    @property
    def w(self):
        return self.box[2] - self.box[0]

    @property
    def h(self):
        return self.box[3] - self.box[1]

    @property
    def cy(self):
        return (self.box[1] + self.box[3]) / 2


def _has_japanese(text: str) -> bool:
    return any(is_kana(c) or is_kanji(c) for c in text)


def _kana_only(text: str) -> bool:
    t = text.strip()
    return bool(t) and all(is_kana(c) or c in " 　・" for c in t)


def _split_furigana(items: list):
    """(candidatos, resto). candidatos = [(item, base, es_furigana)]: cajas pequeñas sobre una línea
    con kanji. `es_furigana` es False si el texto leído no parece kana (γ2, 66...): puede ser un
    furigana mal leído o ruido; se decide en _refine."""
    cands, keep = [], []
    for r in items:
        base = None
        best = None
        if r.h > 0 and r.w > 0:
            for b in items:
                if b is r or not any(is_kanji(c) for c in b.text) or r.h > RUBY_MAX_RATIO * b.h:
                    continue
                overlap = min(r.box[2], b.box[2]) - max(r.box[0], b.box[0])
                gap = b.box[1] - r.box[3]                      # < 0 si se pisan en vertical
                if overlap < RUBY_MIN_OVERLAP * r.w or r.cy >= b.box[1] + 0.3 * b.h or gap > RUBY_MAX_GAP * b.h:
                    continue
                if best is None or abs(gap) < best:
                    best, base = abs(gap), b
        if base is not None and _kana_only(r.text):
            cands.append((r, base, True))
        elif base is not None and (r.score < NOISE_SCORE or not _has_japanese(r.text)):
            cands.append((r, base, False))                     # caja pequeña sobre una línea que no es kana
        else:
            keep.append(r)
    return cands, keep


def _refine(cands: list, img, reader) -> list:
    """[(item, base o None)]: relee con más resolución las cajas pequeñas que no parecen kana.

    El detector encuentra bien la caja de un furigana pequeño (9-12 px) pero a menudo lo lee como
    «γ2», «66» o «s47». Recortando justo al texto y ampliándolo se lee bien (s47 → さが). Si no hay
    lector (o no es modo furigana), o la relectura tampoco da kana, la caja se descarta como ruido.
    Los furigana que ya salieron como kana no se tocan: si el detector se equivocó de kana
    (かぎ → かき), la confianza no permite saber cuál lectura es la buena; eso lo decidirá el diccionario.
    """
    out = []
    for r, base, is_ruby in cands:
        if not is_ruby and reader is not None and img is not None:
            pad_x, pad_y = 0.15 * r.h, 0.25 * r.h
            read = reader(img, (r.box[0] - pad_x, r.box[1] - pad_y, r.box[2] + pad_x, r.box[3] + pad_y))
            if read:
                text, score = clean_reading(read[0]), read[1]
                if text and all(is_kana(c) for c in text) and score >= REFINE_ACCEPT:
                    r.text, r.score, is_ruby = text, score, True
        out.append((r, base if is_ruby else None))
    return out


def _group_rows(items: list) -> list:
    """Agrupa las cajas por fila (mismo renglón, alturas parecidas). Filas de arriba abajo."""
    rows = []                                                  # [[items], y0, y1]
    for it in sorted(items, key=lambda i: i.cy):
        for row in rows:
            ov = min(it.box[3], row[2]) - max(it.box[1], row[1])
            smaller = min(it.h, row[2] - row[1])
            tall_narrow = it.h > 1.5 * it.w and len(it.text) > 1       # texto vertical: no se funde
            if smaller > 0 and ov >= ROW_OVERLAP * smaller and not tall_narrow \
                    and 0.5 <= it.h / (row[2] - row[1]) <= 2.0:
                row[0].append(it)
                row[1], row[2] = min(row[1], it.box[1]), max(row[2], it.box[3])
                break
        else:
            rows.append([[it], it.box[1], it.box[3]])
    rows.sort(key=lambda r: r[1])
    return [sorted(r[0], key=lambda i: i.box[0]) for r in rows]


def _group_columns(items: list) -> list:
    """Modo vertical: agrupa las cajas por columna (mismo ancho de texto, una sobre otra). Las
    columnas salen de derecha a izquierda; dentro de cada una, de arriba abajo."""
    cols = []                                                  # [[items], x0, x1]
    for it in sorted(items, key=lambda i: -(i.box[0] + i.box[2]) / 2):
        for col in cols:
            ov = min(it.box[2], col[2]) - max(it.box[0], col[1])
            if ov >= COLUMN_OVERLAP * min(it.w, col[2] - col[1]) > 0:
                col[0].append(it)
                col[1], col[2] = min(col[1], it.box[0]), max(col[2], it.box[2])
                break
        else:
            cols.append([[it], it.box[0], it.box[2]])
    cols.sort(key=lambda c: -(c[1] + c[2]))
    return [sorted(c[0], key=lambda i: i.box[1]) for c in cols]


def _merge_column(col: list) -> "_Line":
    """Una columna entera como una sola línea (los trozos se pegan sin espacios)."""
    text = "".join(i.text for i in col)
    total = sum(len(i.text) for i in col) or 1
    score = sum(i.score * len(i.text) for i in col) / total
    box = (min(i.box[0] for i in col), min(i.box[1] for i in col),
           max(i.box[2] for i in col), max(i.box[3] for i in col))
    pos, segments = 0, []
    for i in col:
        segments.append((pos, pos + len(i.text), i.box))
        pos += len(i.text)
    return _Line(text, score, box, segments, [])


@dataclass
class _Line:
    text: str
    score: float
    box: tuple
    segments: list                  # [(inicio, fin, caja)] de cada trozo dentro del texto de la línea
    seps: list                      # [(inicio, fin, x0, x1)] huecos con espacio entre trozos


def _merge_row(row: list) -> _Line:
    text, segments, seps = "", [], []
    height = max(i.h for i in row)
    for k, it in enumerate(row):
        if k:
            gap = it.box[0] - row[k - 1].box[2]
            if gap > SPACE_GAP * height:
                seps.append((len(text), len(text) + 1, row[k - 1].box[2], it.box[0]))
                text += " "
        segments.append((len(text), len(text) + len(it.text), it.box))
        text += it.text
    total = sum(len(i.text) for i in row) or 1
    score = sum(i.score * len(i.text) for i in row) / total
    box = (min(i.box[0] for i in row), min(i.box[1] for i in row),
           max(i.box[2] for i in row), max(i.box[3] for i in row))
    return _Line(text, score, box, segments, seps)


def _char_spans(line: _Line) -> list:
    """Posición horizontal estimada (x0, x1) de cada carácter de la línea."""
    spans = [(line.box[0], line.box[0])] * len(line.text)
    for start, end, box in line.segments:
        chunk = line.text[start:end]
        weights = [_weight(c) for c in chunk]
        total = sum(weights) or 1.0
        acc = 0.0
        for k, w in enumerate(weights):
            spans[start + k] = (box[0] + (box[2] - box[0]) * acc / total,
                                box[0] + (box[2] - box[0]) * (acc + w) / total)
            acc += w
    for start, end, x0, x1 in line.seps:
        for k in range(start, end):
            spans[k] = (x0, x1)
    return spans


def _readings_for(line: _Line, rubies: list) -> list:
    """[(inicio, fin, lectura)] de la línea.

    Cada furigana se asocia a los kanji que tiene justo debajo (los que su centro cae dentro del
    ancho del furigana). Puede cubrir solo parte de una cadena de kanji (なりた sobre 成田 en
    成田真理). Si cubre kanji de palabras separadas por kana (長老に話), es ambiguo y se omite.
    """
    if not rubies:
        return []
    spans = _char_spans(line)
    out = []
    for r in rubies:
        covered = []
        for i, c in enumerate(line.text):
            if not is_kanji(c):
                continue
            x0, x1 = spans[i]
            reach = READING_REACH * (x1 - x0)
            if r.box[0] - reach <= (x0 + x1) / 2 <= r.box[2] + reach:
                covered.append(i)
        if not covered:
            continue
        start, end = covered[0], covered[-1] + 1
        if end - start != len(covered) or not all(is_kanji(c) for c in line.text[start:end]):
            continue                                           # hay kana o huecos entre los kanji: ambiguo
        x0, x1 = spans[start][0], spans[end - 1][1]
        if max(x0 - r.box[0], r.box[2] - x1, 0.0) <= READING_OVERHANG * (x1 - x0):
            out.append((start, end, clean_reading(r.text)))
    return sorted(set(out))


# ---------------------------------------------------------------------------- API
def _process(lines: list, boxes: list, mode: Mode, img=None, reader=None):
    """(líneas fusionadas [_Line], lecturas [(línea, inicio, fin, lectura)]). Núcleo común."""
    if len(boxes) != len(lines):                 # sin cajas no se puede ordenar: solo se corrigen caracteres
        fixed = [_Line(fix_chars(t, mode), float(s), (0, 0, 0, 0), [], []) for t, s in lines]
        return fixed, []
    items = [_Item(t, float(s), tuple(b)) for (t, s), b in zip(lines, boxes)
             if t.strip() and not (len(t.strip()) <= STRAY_MAX_LEN and not _has_japanese(t) and float(s) < STRAY_SCORE)]
    rubies = []
    if mode is Mode.VERTICAL:
        merged = [_merge_column(col) for col in _group_columns(items)]
        for line in merged:
            line.text = fix_chars(line.text, mode)
        return merged, []
    if mode is not Mode.KANA:
        cands, items = _split_furigana(items)
        rubies = _refine(cands, img if mode is Mode.FURIGANA else None, reader if mode is Mode.FURIGANA else None)

    merged = _attach_orphans([_merge_row(row) for row in _group_rows(items)])
    for line in merged:
        line.text = fix_chars(line.text, mode)

    readings = []
    if mode is Mode.FURIGANA:
        for li, line in enumerate(merged):
            mine = [r for r, base in rubies if base is not None and
                    line.box[0] - 1 <= base.box[0] and base.box[2] <= line.box[2] + 1 and
                    abs(base.cy - (line.box[1] + line.box[3]) / 2) <= max(base.h, 1)]
            readings += [(li, s, e, text) for s, e, text in _readings_for(line, mine)]
    return merged, readings


def _fill_gaps(img, merged: list, readings: list, reader) -> list:
    """Busca el furigana que el detector no encontró, kanji por kanji.

    Para cada grupo de kanji sin lectura se recorta la franja que hay justo encima y se le pasa a
    `reader(recorte) -> (texto, confianza) o None`, que solo reconoce (sin detectar). Sirve sobre
    todo para furigana de 1 o 2 kana, que el detector suele pasar por alto. Una lectura se acepta
    si son solo kana, tiene confianza suficiente y un número plausible de kana para esos kanji.
    """
    out = list(readings)
    for li, line in enumerate(merged):
        h = line.box[3] - line.box[1]
        if h <= 0 or not line.text:
            continue
        covered = set()
        for l2, s, e, _r in readings:
            if l2 == li:
                covered.update(range(s, e))
        spans = _char_spans(line)
        # el borde inferior de la línea de arriba (si la hay) limita la franja por arriba
        above = [o.box[3] for o in merged if o is not line and o.box[3] <= line.box[1] + 0.3 * h
                 and min(o.box[2], line.box[2]) > max(o.box[0], line.box[0])]
        top = max(line.box[1] - 0.9 * h, max(above) if above else float("-inf"))
        kanji_at = [i for i, c in enumerate(line.text) if is_kanji(c)]
        for m in _KANJI_RUN.finditer(line.text):
            seg = [i for i in range(m.start(), m.end()) if i not in covered]
            while seg:                                           # tramos contiguos sin lectura
                k = 1
                while k < len(seg) and seg[k] == seg[k - 1] + 1:
                    k += 1
                run, seg = seg[:k], seg[k:]
                a, b = run[0], run[-1]
                cw = (spans[b][1] - spans[a][0]) / len(run)
                # La franja llega hasta la mitad del hueco con el kanji vecino (el furigana largo
                # sobresale de su palabra y un margen fijo lo cortaría); pegada si no hay hueco.
                prev = max((i for i in kanji_at if i < a), default=None)
                nxt = min((i for i in kanji_at if i > b), default=None)
                left = (spans[a][0] - GAP_EDGE * cw if prev is None else
                        spans[a][0] if prev == a - 1 else (spans[prev][1] + spans[a][0]) / 2)
                right = (spans[b][1] + GAP_EDGE * cw if nxt is None else
                         spans[b][1] if nxt == b + 1 else (spans[b][1] + spans[nxt][0]) / 2)
                read = reader(img, (left, top, right, line.box[1] - 0.02 * h))
                if not read:
                    continue
                text, score = clean_reading(read[0]), read[1]
                if (score >= GAP_MIN_SCORE and text and all(is_kana(c) for c in text)
                        and len(run) <= len(text) <= GAP_MAX_PER_KANJI * len(run)):
                    out.append((li, a, b + 1, text))
    return sorted(out)


def process_items(lines: list, boxes: list, mode=Mode.NORMAL):
    """Núcleo sin dependencias. `lines` = [(texto, confianza)], `boxes` = [(x0, y0, x1, y1)].

    Devuelve (líneas [(texto, confianza)], cajas, lecturas [(línea, inicio, fin, lectura)]).
    """
    merged, readings = _process(lines, boxes, Mode(mode))
    return [(l.text, l.score) for l in merged], [l.box for l in merged], readings


def process(res, mode=Mode.NORMAL, img=None, reader=None):
    """Igual que process_items pero sobre un ocr_engine.OcrResult. Devuelve otro OcrResult con
    `readings` = [(inicio, fin, lectura)] en posiciones de `text` (vacío salvo en modo FURIGANA).

    En modo FURIGANA, si se pasan la imagen original (`img`, la misma que se le dio al OCR) y un
    `reader` (OcrEngine.read_strip), se vuelven a leer con más resolución los furigana dudosos y se
    rellenan las lecturas que el detector no encontró."""
    mode = Mode(mode)
    merged, per_line = _process(res.lines, res.boxes, mode, img, reader)
    if mode is Mode.FURIGANA and img is not None and reader is not None:
        per_line = _fill_gaps(img, merged, per_line, reader)
    lines = [(l.text, l.score) for l in merged]
    offsets, pos = [], 0
    for text, _score in lines:
        offsets.append(pos)
        pos += len(text) + 1
    readings = [(offsets[li] + s, offsets[li] + e, text) for li, s, e, text in per_line]
    return replace(res, text="\n".join(t for t, _ in lines), lines=lines,
                   boxes=[l.box for l in merged], readings=readings, vertical=mode is Mode.VERTICAL)
