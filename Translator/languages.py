"""Paquetes de idioma: todo lo que cambia según el idioma del texto del juego.

Cada paquete dice cómo dividir en frases, unir líneas partidas, encontrar palabras (para el
mouse), qué modelo traduce y cómo retocar su salida. El resto de la app (traductor, ventana,
capa sobre la pantalla, tarjetas) pregunta al paquete ACTIVO en vez de asumir inglés.

Añadir un idioma = añadir un LanguagePack al diccionario PACKS.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from Translator.ja_text import join_wrapped_lines_ja, sentence_at_ja, split_sentences_ja
from Cards.sentences import SENT_END, sentence_at as sentence_at_latin

MODELS_DIR = Path(__file__).resolve().parent / "models"


# --- inglés (juego en inglés -> traducción al español) ---
_EN_SPLIT = re.compile(rf"(?<=[{re.escape(SENT_END)}])\s+")


def _en_split(line: str) -> list:
    return [s for s in _EN_SPLIT.split(line) if s]


def _en_join(text: str) -> str:
    from Translator.translator import join_wrapped_lines      # import tardío: translator importa este módulo
    return join_wrapped_lines(text)


def _en_iter_words(text: str):
    from UI.word_popup import iter_words_latin
    return iter_words_latin(text)


def _en_find_word(text: str, index: int):
    from UI.word_popup import find_word_latin
    return find_word_latin(text, index)


# --- francés (juego en francés -> traducción al español) ---
_FR_LETTER = r"[^\W\d_]"
_FR_WORD = re.compile(rf"{_FR_LETTER}+['’]?")                       # «l'» (elisión) o palabra
_FR_CHAIN = re.compile(rf"{_FR_LETTER}+(?:['’-]{_FR_LETTER}+)*")    # peut-être, est-ce, c'est-à-dire
_FR_WHOLE = frozenset({"aujourd'hui", "quelqu'un", "quelqu'une", "presqu'île", "prud'homme", "entr'acte",
                       "grand'mère", "grand'chose", "quelqu'uns", "quelques-uns", "quelques-unes"})


def _fr_iter_words(text: str):
    """(inicio, fin, palabra, compuesto). La elisión va aparte («l'amie» -> «l'» + «amie»);
    el compuesto con guiones («peut-être», «dis-moi») se da como contexto para el diccionario."""
    for chain in _FR_CHAIN.finditer(text):
        group = chain.group()
        low = group.lower().replace("’", "'")
        if low in _FR_WHOLE:
            yield chain.start(), chain.end(), group, ""
            continue
        compound = group if "-" in group else ""
        for m in _FR_WORD.finditer(group):
            yield chain.start() + m.start(), chain.start() + m.end(), m.group(), compound


def _fr_find_word(text: str, index: int):
    touching = None
    for item in _fr_iter_words(text):
        if item[0] <= index < item[1]:
            return item
        if index == item[1]:
            touching = touching or item
    return touching


# --- japonés (juego en japonés -> traducción al inglés) ---
def _ja_split(line: str) -> list:
    return [s.text for s in split_sentences_ja(line, merge_lines=False)]


_I_ALONE = re.compile(r"\bi\b")
_SENT_START = re.compile(r"""(^["“'(]*|(?<![.])[.!?]["”']?\s+["“'(]*)([a-z])""")


def fix_english(text: str) -> str:
    """El modelo JA->EN a veces deja la frase en minúscula («let's go!», «i got…»)."""
    text = _I_ALONE.sub("I", text)
    return _SENT_START.sub(lambda m: m.group(1) + m.group(2).upper(), text)


def _identity(text: str) -> str:
    return text


def _no_fixed(text: str):
    return None


def _zero() -> int:
    return 0


def _ja_prepare(text: str) -> str:
    from ja_rules import prepare
    return prepare(text)


def _ja_fixed(text: str):
    from ja_rules import fixed
    return fixed(text)


def _ja_rules_version():
    from ja_rules import version
    return version()


# Palabras japonesas: las segmenta SudachiPy (sin él, no hay palabras bajo el mouse).
def _ja_iter_words(text: str):
    from DB.ja_nlp import tokenizer
    return tokenizer.iter_words(text)


def _ja_find_word(text: str, index: int):
    from DB.ja_nlp import tokenizer
    return tokenizer.find_word(text, index)


@dataclass(frozen=True)
class LanguagePack:
    code: str                    # "en", "ja"
    label: str                   # texto del selector
    source_name: str             # «inglés», «japonés»
    target_name: str             # «español», «inglés»
    model_repo: str              # modelo de traducción ya convertido a CTranslate2
    model_dir: Path
    warmup: str                  # frase corta para calentar el modelo
    split_line: Callable         # una línea -> lista de frases
    join_lines: Callable         # une las líneas partidas a mitad de frase
    sentence_at: Callable        # (texto, posición, unir) -> frase o None
    iter_words: Callable         # texto -> (inicio, fin, palabra, compuesto)
    find_word: Callable          # (texto, posición) -> (inicio, fin, palabra, compuesto) o None
    postprocess: Callable        # retoque de cada frase traducida
    sentence_joiner: str = " "   # cómo se pegan las frases traducidas
    has_ocr_modes: bool = False  # ¿tiene modos de OCR (japonés: normal / solo kana)?
    preprocess: Callable = _identity    # frase -> lo que se le da al modelo (punto final, glosario)
    fixed: Callable = _no_fixed         # frase -> traducción fija del glosario, o None
    rules_version: Callable = _zero     # cambia cuando cambian las reglas (vacía la caché)
    model_alts: tuple = ()              # repositorios alternativos si el principal no se puede descargar
    ocr_lang: str = ""                  # modelo de reconocimiento de RapidOCR ("" = el de serie; "latin")


PACKS = {
    "en": LanguagePack(
        code="en", label="Inglés → Español", source_name="inglés", target_name="español",
        model_repo="michaelfeil/ct2fast-opus-mt-en-es",
        model_dir=MODELS_DIR / "ct2fast-opus-mt-en-es", warmup="Hello",
        split_line=_en_split, join_lines=_en_join, sentence_at=sentence_at_latin,
        iter_words=_en_iter_words, find_word=_en_find_word, postprocess=_identity),
    "ja": LanguagePack(
        code="ja", label="Japonés → Inglés", source_name="japonés", target_name="inglés",
        model_repo="shift-ykurami/fugumt-ja-en-ct2-f32",
        model_dir=MODELS_DIR / "fugumt-ja-en-ct2-f32", warmup="こんにちは",
        split_line=_ja_split, join_lines=join_wrapped_lines_ja, sentence_at=sentence_at_ja,
        iter_words=_ja_iter_words, find_word=_ja_find_word, postprocess=fix_english,
        has_ocr_modes=True),
    "fr": LanguagePack(
        code="fr", label="Francés → Español", source_name="francés", target_name="español",
        model_repo="gaudi/opus-mt-fr-es-ctranslate2",
        model_dir=MODELS_DIR / "opus-mt-fr-es-ctranslate2", warmup="Bonjour",
        split_line=_en_split, join_lines=_en_join, sentence_at=sentence_at_latin,
        iter_words=_fr_iter_words, find_word=_fr_find_word, postprocess=_identity,
        model_alts=("michaelfeil/ct2fast-opus-mt-fr-es",), ocr_lang="latin"),
}

_active = PACKS["en"]


def get_pack(code: str) -> LanguagePack:
    return PACKS[code]


def active() -> LanguagePack:
    return _active


def set_active(code: str) -> LanguagePack:
    global _active
    _active = PACKS[code]
    return _active
