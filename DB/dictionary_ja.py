"""Diccionario japonés local (JMdict en SQLite, ver build_dictionary_ja.py).

Misma interfaz que dictionary.Dictionary, así la ventana, el popup y las tarjetas lo usan igual:

    d = JaDictionary()
    result = d.lookup("開け", "開ける")   # (palabra del texto, forma de diccionario)
    result.entries[0].lemma, result.entries[0].note   # "開ける", "あける" (la lectura)

La lectura sale del diccionario (ya no hace falta el furigana). Para elegir entre homógrafos
(は «partícula» / 歯 «diente») se usa la categoría gramatical que dio SudachiPy en el texto.
"""
import re
import sqlite3
from pathlib import Path

from DB.dictionary import Entry, LookupResult, Sense
from DB.ja_nlp import normalize, to_hiragana, tokenizer
from DB.levels_ja import Levels

DB_PATH = Path(__file__).resolve().parent / "data" / "dictionary_ja.db"

MAX_ENTRIES = 3             # candidatos máximos por palabra
SENSES_SINGLE = 5           # significados cuando solo hay una entrada
SENSES_MULTI = (3, 2, 1)    # significados por entrada, en orden, cuando hay varias
FULL_SINGLE = 30            # con full=True (ventanita desplazable): prácticamente todos
FULL_MULTI = (30, 10, 6)

# Etiquetas JMdict -> texto corto en español
_EXACT = {
    "n": "sust.", "n-adv": "sust. adv.", "n-t": "sust. temporal", "n-suf": "sufijo", "n-pref": "prefijo",
    "n-pr": "nombre propio", "pn": "pronombre", "prt": "partícula", "conj": "conjunción",
    "int": "interjección", "ctr": "contador", "suf": "sufijo", "pref": "prefijo", "cop": "cópula",
    "exp": "expresión", "num": "numeral", "adj-i": "adj. い", "adj-ix": "adj. い", "adj-na": "adj. な",
    "adj-no": "adj. の", "adj-pn": "adnominal", "adv": "adv.", "adv-to": "adv.", "vk": "verbo kuru",
    "vz": "verbo", "v-unspec": "verbo",
}
_PREFIXES = [("vs", "verbo suru"), ("v5", "verbo (godan)"), ("v1", "verbo (ichidan)"),
             ("v2", "verbo (arcaico)"), ("v4", "verbo (arcaico)"), ("aux", "auxiliar"),
             ("adj", "adj."), ("adv", "adv."), ("n-", "sust."), ("v", "verbo")]
_TRANSITIVITY = {"vt": "trans.", "vi": "intrans."}
_CONTENT_TAGS = ("n", "v", "adj", "adv", "pn", "suf", "pref", "ctr", "num", "exp")   # no son palabras gramaticales


def _tag_label(tag: str) -> str:
    if tag in _EXACT:
        return _EXACT[tag]
    return next((label for prefix, label in _PREFIXES if tag.startswith(prefix)), "")


def pos_label(tags: str) -> str:
    """«v5r vt» -> «verbo (godan), trans.»"""
    parts = [t for t in tags.split() if t not in _TRANSITIVITY and t != "unc"]
    main = next((lbl for lbl in map(_tag_label, parts) if lbl), "")
    extra = [_TRANSITIVITY[t] for t in tags.split() if t in _TRANSITIVITY]
    return ", ".join(x for x in [main] + extra if x)


def _score(tags: str, prefixes: tuple) -> int:
    """Cuánto encajan las categorías `tags` con la pista (los prefijos van de más a menos específicos)."""
    parts = tags.split()
    for i, p in enumerate(prefixes):
        if any(t == p or t.startswith(p) for t in parts):
            return len(prefixes) - i
    return 0


_SMALL = str.maketrans("ぁぃぅぇぉゎ", "あいうえおわ")
_ROWS = {"あ": "かがさざただなはばぱまやらわ", "い": "きぎしじちぢにひびぴみり", "う": "くぐすずつづぬふぶぷむゆる",
         "え": "けげせぜてでねへべぺめれ", "お": "こごそぞとどのほぼぽもよろを"}
_ROW_OF = {c: vowel for vowel, chars in _ROWS.items() for c in chars}
_E_ROW_TO_I = {}          # け -> [か, き, こ, く]: filas que pueden acabar en «～い» y cambiar a «～え»
for _e in _ROWS["え"]:
    _E_ROW_TO_I[_e] = []
for _i, _e in enumerate(_ROWS["え"]):
    for _v in ("あ", "い", "う", "お"):
        _E_ROW_TO_I[_e].append(_ROWS[_v][_i] if _i < len(_ROWS[_v]) else "")
_VOWEL_OF_SMALL_Y = {"ゃ": "あ", "ゅ": "う", "ょ": "お"}


def _variants(text: str) -> list:
    """Formas más corrientes de una escritura coloquial: ぎゃー -> ぎゃあ, ふぅ -> ふう, ピタッ -> ぴたっと,
    ばーか -> ばか, すげえ -> すごい. Solo se prueban cuando la palabra tal cual no está."""
    out = []
    h = to_hiragana(text)
    small = h.translate(_SMALL)
    if small != h:
        out.append(small)
    if "ー" in h:
        # ー detrás de una kana: la vocal que alarga (ぎゃー -> ぎゃあ), o se quita (ばーか -> ばか)
        longer = []
        for i, c in enumerate(h):
            if c == "ー" and i:
                p = h[i - 1]
                vowel = _VOWEL_OF_SMALL_Y.get(p) or _ROW_OF.get(p) or (p if p in "あいうえお" else "")
                longer.append(vowel or "")
            else:
                longer.append(c)
        out.append("".join(longer))
        out.append(h.replace("ー", ""))
    if h.endswith(("っ", "ッ")):
        out += [h[:-1] + "っと", h[:-1] + "り"]
    n = small
    if len(n) >= 2 and n.endswith("え") and n[-2] in _E_ROW_TO_I:
        cands = [c for c in _E_ROW_TO_I[n[-2]] if c]
        if n[-2] == "げ":                      # すげえ -> すごい antes que すがい
            cands.sort(key=lambda c: c != "ご")
        out += [n[:-2] + c + "い" for c in cands]
    return [v for v in dict.fromkeys(out) if v and v != text]


class JaDictionary:
    build_hint = "build_dictionary_ja.py"      # script que lo genera (lo muestra el popup si falta)

    def __init__(self, path: Path = DB_PATH, levels_path=None):
        self.path = Path(path).resolve()
        self._con = None
        self.levels = Levels(levels_path) if levels_path else Levels()   # nivel JLPT y frecuencias (opcional)
        if self.path.exists():
            self._con = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)

    @property
    def available(self) -> bool:
        return self._con is not None

    def close(self):
        self.levels.close()
        if self._con is not None:
            self._con.close()
            self._con = None

    # --- API pública (igual que Dictionary) ---
    def lookup(self, word: str, compound: str = "", full: bool = False):
        """`word` es lo escrito en el texto y `compound` su forma de diccionario ("" si es igual).
        `full` pide muchos más significados (para la ventanita desplazable)."""
        if not self.available:
            return None
        hint = tokenizer.pos_hint(word, compound)
        fallback = None
        for text in self._candidates(word, compound):
            rows = self._rows(text, hint)
            if not rows:
                continue
            if not hint or rows[0][0]:                  # la categoría coincide (o no hay pista)
                return self._result(word, text, rows, full)
            fallback = fallback or (text, rows)
        if fallback:
            return self._result(word, *fallback, full)
        return None

    # --- reparación de la ー que el OCR a veces se come (ロド -> ロード) ---
    _KATA_RUN = re.compile("[ァ-ヶー]{2,}")
    _NO_LONG = frozenset("ンッー・ャュョァィゥェォヮヶヵ")      # tras estos no se alarga la vocal

    def has_form(self, text: str) -> bool:
        return self.available and self._con.execute(
            "SELECT 1 FROM form WHERE text=? LIMIT 1", (text,)).fetchone() is not None

    def repair_katakana(self, text: str) -> str:
        """Si una palabra en katakana no existe en JMdict pero con UNA «ー» más sí (y solo de una
        manera), usa esa: «ロド完了» -> «ロード完了», «ポション» -> «ポーション». Los nombres propios
        que no se parecen a ninguna palabra se dejan tal cual."""
        if not self.available:
            return text

        def fix(m):
            run = m.group()
            if self.has_form(run):
                return run
            found = []
            for i in range(1, len(run) + 1):
                if run[i - 1] in self._NO_LONG or (i < len(run) and run[i] == "ー"):
                    continue
                cand = run[:i] + "ー" + run[i:]
                if self.has_form(cand) and cand not in found:
                    found.append(cand)
            return found[0] if len(found) == 1 else run
        return self._KATA_RUN.sub(fix, text)

    def key_for(self, word: str, compound: str = "") -> str:
        """Clave de la tarjeta: la forma de diccionario (勝った y 勝つ son la misma tarjeta)."""
        text = (compound or word).strip()
        h = to_hiragana(text)
        if h != text and self.available and not self.has_form(text) and self.has_form(h):
            return h                                    # キミ y きみ son la misma tarjeta
        return text

    # --- interno ---
    @staticmethod
    def _candidates(word: str, compound: str) -> list:
        out = []
        for t in (compound, word, normalize(compound), normalize(word)):
            if t and t not in out:
                out.append(t)
        # Katakana de énfasis o de estilo (キミ, ワケ, プツプツ): JMdict solo los trae en hiragana o
        # con kanji. Se prueban al final, así las palabras que sí existen en katakana no cambian.
        for t in list(out):
            h = to_hiragana(t)
            if h != t and h not in out:
                out.append(h)
        # Variantes de escritura coloquial (alargamientos, ッ final, vocales pequeñas, ～ええ por ～い).
        for t in list(out):
            for v in _variants(t):
                if v not in out:
                    out.append(v)
        return out

    def _rows(self, text: str, hint: tuple) -> list:
        """[(coincide_categoría, común, id, lectura, kanji, kana, pos, nsense)] mejor primero."""
        rows = self._con.execute(
            "SELECT f.entry_id, f.reading, f.common, e.kanji, e.kana, e.pos, e.nsense "
            "FROM form f JOIN entry e ON e.id = f.entry_id WHERE f.text = ?", (text,)).fetchall()
        seen, out = set(), []
        for order, (eid, reading, common, kanji, kana, pos, nsense) in enumerate(rows):
            if eid in seen:                              # una entrada con varias lecturas: la primera
                continue
            seen.add(eid)
            out.append((_score(pos, hint) if hint else 0, common, nsense, order,
                        eid, reading, kanji, kana, pos))
        out.sort(key=lambda r: (-r[0], -r[1], -r[2], r[3]))
        return [(r[0], r[1], r[4], r[5], r[6], r[7], r[8], r[2]) for r in out]

    def _result(self, word: str, text: str, rows: list, full: bool = False) -> LookupResult:
        if rows and rows[0][0] > 0:                     # con pista de categoría: solo las que mejor encajan
            rows = [r for r in rows if r[0] == rows[0][0]]
        rows = rows[:MAX_ENTRIES]
        single, multi = (FULL_SINGLE, FULL_MULTI) if full else (SENSES_SINGLE, SENSES_MULTI)
        limits = (single,) if len(rows) == 1 else multi
        hint = tokenizer.pos_hint(word, text if text != word else "")
        entries = []
        for i, (_m, _c, eid, reading, kanji, kana, pos, nsense) in enumerate(rows):
            limit = limits[min(i, len(limits) - 1)]
            sense_rows = self._con.execute(
                "SELECT pos, gloss FROM sense WHERE entry_id=? ORDER BY n", (eid,)).fetchall()
            if hint:                                    # primero los significados de la categoría del texto
                sense_rows.sort(key=lambda r: -_score(r[0], hint))
            senses = [Sense(pos_label(p), "; ".join(g.split("|")[:6])) for p, g in sense_rows[:limit]]
            read, note = "", ""
            if text == reading:                         # buscado por kana: mostrar cómo se escribe con kanji
                function_word = not any(t.startswith(_CONTENT_TAGS) for t in pos.split())
                if kanji and not function_word:
                    note = "kanji: " + ", ".join(kanji.split("|")[:2])
            else:
                read = reading                          # buscado por kanji: la lectura va al lado
            entries.append(Entry(lemma=text, senses=senses, total=nsense, note=note, reading=read,
                                 tags=self.levels.tags(text, reading)))
        return LookupResult(query=word, entries=entries)
