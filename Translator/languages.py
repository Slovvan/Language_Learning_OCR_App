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
