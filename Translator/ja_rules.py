"""Reglas propias de la traducción japonés -> inglés (se activan en Configuración ▸ Traducción).

1. Punto final inteligente. Los fragmentos con verbo sin puntuación salen mejor con «。» al final
   («経験値を獲得した»: «Empirical» -> «I got experience.»), pero las etiquetas de menú sufren
   («オプション»: «Option» -> «Optional.»). Por eso el punto solo se añade si parece una oración.
2. Números en kanji -> cifras. El modelo falla con los numerales compuestos («千五百円»: «150 yen»,
   «二・五パーセント»: «two or five percent»). Se escriben con cifras antes de traducir («1,500円»,
   «2.5パーセント»). Solo se tocan los números compuestos seguidos de una unidad; palabras como 一緒,
   一人, 十分, 三日月 o 一時停止 se dejan como están.

Solo cambia lo que se le da al modelo: el texto reconocido (y lo que ves en el panel) no se modifica.
"""
import re

                                   # tu proyecto (subpaquetes) o la carpeta plana
from Translator.ja_text import LINE_END

_SENTENCE_POS = frozenset({"動詞", "助動詞", "形容詞"})     # el texto termina en esto -> es una oración
_ASCII_END = ".!?…"
# Partículas que marcan una oración. «の», «と», «や», «か»... también aparecen en nombres
# («勇者の剣», «武器と防具») y ahí un punto empeora la traducción («It's the hero's sword.»).
_CLAUSE_PARTICLES = frozenset({"を", "が", "は", "も", "に", "で", "へ", "から", "まで", "って", "けど", "ので", "のに"})

ENABLED = True            # Configuración ▸ Traducción: reglas del japonés activadas (sí de fábrica)


def set_enabled(on: bool):
    global ENABLED
    ENABLED = bool(on)


def is_enabled() -> bool:
    return ENABLED


# ---------------------------------------------------------------------------------------------- punto final
def looks_like_sentence(text: str) -> bool:
    """¿Parece una oración y no una etiqueta de menú o un nombre?

    - Termina en verbo/adjetivo y tiene al menos 2 palabras («経験値を獲得した»). Un verbo suelto
      («戻る», «買う») es un comando de menú: se queda sin punto.
    - O lleva una partícula de oración (を, が, は, に...) antes de la última palabra («MPが足りない»).
    - «の» y «と» no cuentan: «勇者の剣», «武器と防具» son nombres.
    """
    from DB.ja_nlp import tokenizer
    tokens = [t for t in tokenizer.tokens(text) if t.pos not in ("補助記号", "空白", "記号")]
    if not tokens:
        return False
    if tokens[-1].pos in _SENTENCE_POS:
        return len(tokens) >= 2
    return any(t.pos == "助詞" and t.surface in _CLAUSE_PARTICLES for t in tokens[:-1])


def needs_period(text: str) -> bool:
    text = text.strip()
    if not text or text[-1] in LINE_END or text[-1] in _ASCII_END:
        return False
    return looks_like_sentence(text)


# ---------------------------------------------------------------------------------------------- números
_DIGIT = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SMALL = {"十": 10, "百": 100, "千": 1000}
_BIG = {"万": 10 ** 4, "億": 10 ** 8, "兆": 10 ** 12}
_NUM_CHARS = "〇零一二三四五六七八九十百千万億兆"
# Unidades que siguen a un número (las más largas primero). Con «第» delante también valen (第二十章).
_UNITS = ("平方キロメートル", "平方メートル", "パーセント", "ヶ月", "か月", "カ月", "週間", "日間", "年間", "時間", "分間", "世紀", "メートル", "センチ", "ミリ",
          "リットル", "キロ", "グラム", "ドル", "ユーロ", "円", "年", "月", "日", "時", "分", "秒", "歳", "才", "人", "名", "個", "本",
          "枚", "台", "冊", "匹", "頭", "羽", "杯", "回", "倍", "点", "位", "階", "号", "章", "話", "巻", "条", "期", "件", "軒", "戸",
          "度", "％", "%")
# Lo que NO puede venir justo después de la unidad (son otras palabras): «三日月», «四分の一», «五分五分»...
_NOT_AFTER = {"日": "月", "月": "曜", "分": "野別解析割裂岐の", "時": "代計"}
_NUM_RE = re.compile(
    rf"(?<![{_NUM_CHARS}])([{_NUM_CHARS}]+(?:・[{_NUM_CHARS}]+)?)(?=({'|'.join(re.escape(u) for u in _UNITS)}))")
# «十分» es «bastante» o «10 minutos»: solo son minutos con estas palabras detrás.
_JUUBUN_MINUTES = re.compile(r"^分(くらい|ぐらい|ほど|程|間|かかる|以内|前|後|おき|ごと|遅れ|早く)")


def _to_int(run: str):
    """Valor de un numeral kanji («千五百» -> 1500, «二〇二六» -> 2026) o None si no es un número válido."""
    if all(c in _DIGIT for c in run):                        # posicional: «二〇二六»
        return int("".join(str(_DIGIT[c]) for c in run))
    total = section = digit = 0
    for c in run:
        if c in _DIGIT:
            digit = _DIGIT[c] if digit == 0 else digit * 10 + _DIGIT[c]
        elif c in _SMALL:
            section += (digit or 1) * _SMALL[c]
            digit = 0
        elif c in _BIG:
            total += (section + digit or 1) * _BIG[c]
            section = digit = 0
        else:
            return None
    return total + section + digit


# Con estas unidades las cifras de 4 o más dígitos llevan separador de miles («1,500円», «3,776メートル»), como se escribe en japonés;
# años, fechas, horas, pisos y capítulos van sin él («2026年»).
_COMMA_UNITS = frozenset({"円", "ドル", "ユーロ", "人", "名", "個", "本", "枚", "台", "冊", "匹", "頭", "羽", "杯", "回", "倍", "点", "件", "軒", "戸",
                          "キロ", "メートル", "センチ", "ミリ", "グラム", "リットル", "平方キロメートル", "平方メートル", "度"})
_SINGLE_OK_UNITS = ("円", "ドル", "ユーロ", "パーセント", "％", "%", "キロ", "メートル", "センチ", "ミリ", "グラム", "リットル")


def _convert(run: str, unit: str = ""):
    """Texto en cifras de un numeral (con decimales «二・五» -> «2.5») o None si no conviene tocarlo."""
    if "・" in run:
        left, right = run.split("・")
        if not all(c in _DIGIT for c in right):
            return None
        a = _to_int(left)
        return None if a is None else f"{a}." + "".join(str(_DIGIT[c]) for c in right)
    if run in ("万一", "一万一"):                              # «por si acaso», no un número
        return None
    if all(c in _DIGIT for c in run):                        # «二〇二六»; «二三» («unos dos o tres») no se toca
        return str(_to_int(run)) if ("〇" in run or "零" in run or len(run) >= 3) else None
    if len(run) < 2 and run != "十" and not (run in _SMALL or run in _BIG) :
        return None
    if len(run) < 2 and run in ("百", "千", "万") and unit not in _SINGLE_OK_UNITS:     # 万能, 百科, 百人一首...
        return None
    value = _to_int(run)
    if value is None:
        return None
    return f"{value:,}" if value >= 1000 and unit in _COMMA_UNITS else str(value)


# Fecha y hora: «三月十五日», «二時間三十分», «三時十五分». Lo que va delante de otro número ya en cifras también se escribe en cifras
# («三月15日» mezclado confunde al modelo). Solos («三月», «二時間») se dejan: el modelo los entiende.
_LEAD_UNIT = re.compile(r"(?<![%s])([一二三四五六七八九])(月|時間|時)(?=[0-9]{1,4}(?:日|分|秒|月|時|年))" % _NUM_CHARS)
# Un numeral compuesto sin unidad pero seguido de kana o signos («レベルが二十に上がった», «二百五十のダメージ»); si le sigue
# un kanji se deja (五十音, 十二支, 三十路, 千代田).
_FREE_RUN = re.compile(r"(?<![%s])([%s]{2,})(?![\u3400-\u9fff々〆ヶ%s])" % (_NUM_CHARS, _NUM_CHARS, _NUM_CHARS))


def numerals_to_digits(text: str) -> str:
    """Escribe con cifras los numerales kanji compuestos (con unidad, o seguidos de kana) y las fechas y horas."""
    text = _with_unit(text)
    text = _FREE_RUN.sub(lambda m: _convert(m.group(1)) or m.group(1), text)
    return _LEAD_UNIT.sub(lambda m: f"{_DIGIT[m.group(1)]}{m.group(2)}", text)


def _with_unit(text: str) -> str:
    out, pos = [], 0
    for m in _NUM_RE.finditer(text):
        run = m.group(1)
        unit = next(u for u in _UNITS if text.startswith(u, m.end()))      # la unidad más larga que coincide
        after = text[m.end() + len(unit): m.end() + len(unit) + 1]
        if after and after in _NOT_AFTER.get(unit, ""):
            continue
        if run == "十" and unit == "分" and not _JUUBUN_MINUTES.match(text[m.end():]):
            continue
        digits = _convert(run, unit)
        if digits is None:
            continue
        out.append(text[pos:m.start(1)])
        out.append(digits)
        pos = m.end(1)
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------------------------- interfaz
def prepare(text: str) -> str:
    """Entrada que se le da al modelo: números en cifras y punto final si es una oración."""
    if not ENABLED:
        return text
    text = numerals_to_digits(text)
    return text + "。" if needs_period(text) else text


def fixed(text: str):
    """(Gancho de LanguagePack.fixed) Ya no hay traducciones fijas: el glosario se quitó; siempre None."""
    return None


def version():
    """Cambia al activar o desactivar las reglas (vacía la caché de traducciones)."""
    return 0 if ENABLED else -1
