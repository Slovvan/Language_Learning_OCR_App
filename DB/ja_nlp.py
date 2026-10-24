"""Análisis del texto japonés con SudachiPy: dónde empieza y acaba cada palabra y cuál es su
forma de diccionario (勝った -> 勝つ).

    from ja_nlp import tokenizer
    for start, end, surface, lemma in tokenizer.iter_words("この扉はまだ開けられない。"):
        ...

Usa la forma (inicio, fin, palabra, compuesto) que ya esperan la ventana y la capa sobre la
pantalla. En «compuesto» va la forma de diccionario cuando difiere de lo escrito; así el
diccionario y las tarjetas reciben el lema sin cambiar ninguna otra interfaz.
Si SudachiPy no está instalado, `available` es False y no se encuentra ninguna palabra.
"""
import functools
import unicodedata
from dataclasses import dataclass

try:
    from sudachipy import dictionary as _sudachi_dictionary
    from sudachipy import tokenizer as _sudachi_tokenizer
except Exception:  # noqa: BLE001  (no instalado o sin diccionario)
    _sudachi_dictionary = _sudachi_tokenizer = None

SKIP_POS = frozenset({"補助記号", "空白", "記号"})          # signos y espacios: no son palabras

# Categoría de Sudachi -> prefijos de las categorías de JMdict que le corresponden. Sirve para
# elegir la entrada correcta entre homógrafos (は «partícula» y 歯 «diente» se leen igual).
POS_TO_JMDICT = {
    "助詞": ("prt", "conj"),
    "助動詞": ("aux", "cop"),
    "動詞": ("v",),
    "名詞": ("n",),
    "代名詞": ("pn",),
    "形容詞": ("adj-i",),
    "形状詞": ("adj-na", "adj-no", "adj-t", "adj-f", "n"),
    "副詞": ("adv",),
    "接続詞": ("conj",),
    "感動詞": ("int",),
    "連体詞": ("adj-pn",),
    "接尾辞": ("suf", "n-suf", "ctr"),
    "接頭辞": ("pref", "n-pref"),
}


def _hint_for(parts) -> tuple:
    """Prefijos de categoría JMdict para un token, de más a menos específico.

    Los detalles de Sudachi afinan la elección: 来る es «カ行変格» (vk), no el 繰る (v5) que se lee igual;
    «て» tras un verbo es una partícula conjuntiva, no la «て» de las citas.
    """
    hint = []
    conj_type = parts[4] if len(parts) > 4 else ""
    if "カ行変格" in conj_type:
        hint.append("vk")
    elif "サ行変格" in conj_type:
        hint.append("vs")
    elif "五段" in conj_type:
        hint.append("v5")
    elif "一段" in conj_type:
        hint.append("v1")
    if parts[0] == "助詞" and parts[1] == "接続助詞":
        hint.extend(["conj", "prt"])
    else:
        hint.extend(POS_TO_JMDICT.get(parts[0], ()))
    return tuple(dict.fromkeys(hint))


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    surface: str
    lemma: str        # forma de diccionario ("" si no hay distinta de surface)
    reading: str      # lectura en katakana según Sudachi
    pos: str          # categoría principal de Sudachi (名詞, 動詞...)


class JaTokenizer:
    def __init__(self):
        self._tk = None
        self._failed = False
        self._hints = {}                     # (superficie, lema) -> prefijos de categorías JMdict

    @property
    def available(self) -> bool:
        return _sudachi_dictionary is not None and not self._failed

    def _get(self):
        if self._tk is None:
            d = _sudachi_dictionary.Dictionary()
            make = getattr(d, "tokenizer", None) or d.create
            self._tk = make()
            self._mode = _sudachi_tokenizer.Tokenizer.SplitMode.B      # palabras de tamaño medio
        return self._tk

    def tokens(self, text: str) -> tuple:
        if not self.available or not text.strip():
            return ()
        try:
            return _tokens(self, text)
        except Exception:  # noqa: BLE001
            self._failed = True              # diccionario de Sudachi roto: se desactiva sin romper la app
            return ()

    def _tokenize(self, text: str) -> tuple:
        out = []
        for m in self._get().tokenize(text, self._mode):
            pos = m.part_of_speech()[0]
            surface = m.surface()
            lemma = m.dictionary_form()
            out.append(Token(m.begin(), m.end(), surface, lemma if lemma != surface else "",
                             m.reading_form(), pos))
            prefixes = _hint_for(m.part_of_speech())
            if prefixes:
                key = (surface, lemma)
                self._hints[key] = tuple(dict.fromkeys(self._hints.get(key, ()) + prefixes))
        return tuple(out)

    def pos_hint(self, word: str, lemma: str = "") -> tuple:
        """Prefijos de categoría JMdict de la última vez que se vio esta palabra en un texto."""
        return self._hints.get((word, lemma or word), ())

    # --- forma que usa la interfaz: (inicio, fin, palabra, compuesto) ---
    def iter_words(self, text: str):
        for t in self.tokens(text):
            if t.pos in SKIP_POS or not t.surface.strip():
                continue
            yield t.start, t.end, t.surface, t.lemma

    def find_word(self, text: str, index: int):
        """Palabra que contiene la posición `index`; si cae justo al final de una, esa (como la
        versión latina)."""
        touching = None
        for item in self.iter_words(text):
            if item[0] <= index < item[1]:
                return item
            if index == item[1]:
                touching = touching or item
        return touching


@functools.lru_cache(maxsize=512)
def _tokens(owner: JaTokenizer, text: str) -> tuple:
    return owner._tokenize(text)


def to_hiragana(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


tokenizer = JaTokenizer()
