"""Exportación de tarjetas a Anki: archivo de texto (se importa desde Anki) y paquete .apkg.

Cada idioma del juego tiene su propio perfil (campos, tipo de nota y mazo):

    inglés   Word | Meaning | Sentence | SentenceTranslation | Extra
    japonés  Word | Reading | Meaning | Sentence | SentenceTranslation | Extra

- Archivo de texto (.txt): no necesita nada más. En Anki: Archivo > Importar, y en el cuadro
  elige tu tipo de nota (por ejemplo el de tu mazo de japonés) y asigna cada columna a un
  campo. Si vuelves a importar, Anki actualiza las notas cuya primera columna (la palabra)
  ya existe en lugar de duplicarlas.
- Paquete (.apkg): necesita `pip install genanki`. Crea un tipo de nota y un mazo propios de
  cada idioma («OCR Translator – Vocabulario» y «OCR Translator – Japonés») con su plantilla
  (anverso: la palabra; reverso: lectura, significado, frase con la palabra en negrita y su
  traducción). Cada nota lleva un identificador estable, así que importar de nuevo actualiza
  las notas existentes.
"""
import csv
import hashlib
import html
import re
from pathlib import Path

from DB.levels_ja import fine_band

TAG = "OCR_Translator"
FIELDS = ["Word", "Meaning", "Sentence", "SentenceTranslation", "Extra"]      # inglés (compatibilidad)

_BASE_CSS = """
.card { font-family: Arial, "Noto Sans JP", "Yu Gothic", Meiryo, sans-serif; font-size: 20px; text-align: center; }
.word { font-size: 34px; font-weight: bold; }
.reading { font-size: 24px; color: #1f4f8f; margin-top: 4px; }
.meaning { text-align: left; display: inline-block; margin: 6px 0; }
.sentence { margin-top: 14px; font-size: 22px; }
.sentence b { color: #d9534f; }
.translation { margin-top: 4px; color: #4a7c59; }
.extra { margin-top: 12px; font-size: 16px; color: #888; }
"""
_QFMT = '<div class="word">{{Word}}</div>'
_TAIL = (
    '<div class="meaning">{{Meaning}}</div>'
    '{{#Sentence}}<div class="sentence">{{Sentence}}</div>'
    '<div class="translation">{{SentenceTranslation}}</div>{{/Sentence}}'
    '{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}'
)

# Identificadores fijos: Anki reconoce el mismo tipo de nota y el mismo mazo en cada importación.
PROFILES = {
    "en": {
        "fields": ["Word", "Meaning", "Sentence", "SentenceTranslation", "Extra"],
        "model_id": 1830629467, "deck_id": 1830629468,
        "model_name": "OCR Translator – Vocabulario", "deck_name": "OCR Translator",
        "template": "Palabra → significado",
        "qfmt": _QFMT, "afmt": '{{FrontSide}}<hr id="answer">' + _TAIL,
        "guid_prefix": "ocr-translator:",          # igual que antes: las notas ya importadas se actualizan
        "word_boundaries": True,
    },
    "ja": {
        "fields": ["Word", "Reading", "Meaning", "Sentence", "SentenceTranslation", "Extra"],
        "model_id": 1830629469, "deck_id": 1830629470,
        "model_name": "OCR Translator – Japonés", "deck_name": "OCR Translator – Japonés",
        "template": "Palabra → lectura y significado",
        "qfmt": _QFMT,
        "afmt": ('{{FrontSide}}<hr id="answer">'
                 '{{#Reading}}<div class="reading">{{Reading}}</div>{{/Reading}}' + _TAIL),
        "guid_prefix": "ocr-translator:ja:",
        "word_boundaries": False,                  # en japonés no hay espacios: se busca el texto tal cual
    },
    "fr": {
        "fields": ["Word", "Meaning", "Sentence", "SentenceTranslation", "Extra"],
        "model_id": 1830629471, "deck_id": 1830629472,
        "model_name": "OCR Translator – Francés", "deck_name": "OCR Translator – Francés",
        "template": "Palabra → significado",
        "qfmt": _QFMT, "afmt": '{{FrontSide}}<hr id="answer">' + _TAIL,
        "guid_prefix": "ocr-translator:fr:",
        "word_boundaries": True,
    },
}
CSS = _BASE_CSS
# Compatibilidad con la versión anterior (solo inglés)
MODEL_ID, DECK_ID = PROFILES["en"]["model_id"], PROFILES["en"]["deck_id"]
MODEL_NAME, DECK_NAME = PROFILES["en"]["model_name"], PROFILES["en"]["deck_name"]
QFMT, AFMT = PROFILES["en"]["qfmt"], PROFILES["en"]["afmt"]


def profile_for(cards, lang=None) -> dict:
    """Perfil del idioma `lang`; si no se da, el de la primera tarjeta (por defecto, inglés)."""
    cards = list(cards)
    lang = lang or (cards[0].lang if cards and getattr(cards[0], "lang", "") else "en")
    return PROFILES.get(lang, PROFILES["en"])


def _esc(text: str) -> str:
    """Texto plano -> HTML: escapa <, > y &, y convierte los saltos de línea en <br>."""
    text = text.replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(text, quote=False).replace("\n", "<br>")


def _html(text: str) -> str:
    return _esc(text.strip())


def sentence_html(card) -> str:
    """Frase con la palabra en negrita (primera aparición de la palabra tal como apareció)."""
    text, surface = card.sentence.strip(), card.surface.strip()
    if surface:
        pattern = re.escape(surface)
        if profile_for([card])["word_boundaries"]:
            pattern = rf"(?<!\w){pattern}(?!\w)"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return _esc(text[:m.start()]) + "<b>" + _esc(m.group()) + "</b>" + _esc(text[m.end():])
    return _esc(text)


def card_fields(card, lang=None) -> list:
    """Los campos de la nota, ya en HTML, en el orden del perfil del idioma."""
    prof = profile_for([card], lang)
    values = {
        "Word": _html(card.word), "Reading": _html(getattr(card, "reading", "")),
        "Meaning": _html(card.meaning), "Sentence": sentence_html(card),
        "SentenceTranslation": _html(card.sentence_es), "Extra": _html(card.extra),
    }
    return [values[name] for name in prof["fields"]]


def anki_tags(card) -> list:
    """Etiquetas de la nota: la general y, si hay datos, el nivel y las frecuencias
    («JLPT N3» -> JLPT::N3, «Anime #1 234» -> freq::anime::top_1500)."""
    out = [TAG]
    for part in (getattr(card, "tags", "") or "").split(" · "):
        part = part.strip()
        if part.startswith("JLPT "):
            out.append("JLPT::" + part[5:].strip())
        elif " #" in part:                                  # «Anime #1 234»: puesto exacto -> banda fina
            label, rank = part.split(" #", 1)
            digits = re.sub(r"\D", "", rank)
            if digits:
                out.append(f"freq::{re.sub(r'[ ]+', '_', label.strip().lower())}::{fine_band(int(digits))}")
        elif ":" in part:                                   # formato anterior: «Anime: top 5 000»
            label, rank = part.split(":", 1)
            digits = re.sub(r"\D", "", rank)
            rank = ("mas_de_" if "más" in rank else "top_") + digits
            out.append(f"freq::{re.sub(r'[ ]+', '_', label.strip().lower())}::{rank}")
    return out


def note_guid(card, lang=None) -> str:
    """Identificador estable de la nota (depende solo del idioma y la clave de la tarjeta)."""
    prefix = profile_for([card], lang)["guid_prefix"]
    return hashlib.sha1(f"{prefix}{card.key}".encode("utf-8")).hexdigest()[:10]


def export_tsv(cards, path, lang=None) -> int:
    """Escribe un archivo de texto para importar en Anki. Devuelve cuántas tarjetas escribió."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cards = list(cards)
    prof = profile_for(cards, lang)
    with open(path, "w", encoding="utf-8", newline="") as f:
        # Cabeceras de Anki (2.1.54 o posterior): separador, HTML permitido y columna de etiquetas.
        f.write(f"#separator:tab\n#html:true\n#tags column:{len(prof['fields']) + 1}\n")
        writer = csv.writer(f, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        for card in cards:
            writer.writerow(card_fields(card, lang) + [" ".join(anki_tags(card))])
    return len(cards)


def export_apkg(cards, path, lang=None) -> int:
    """Escribe un paquete .apkg. Lanza ImportError si falta genanki."""
    import genanki  # import tardío: es opcional

    cards = list(cards)
    prof = profile_for(cards, lang)
    model = genanki.Model(
        prof["model_id"], prof["model_name"],
        fields=[{"name": name} for name in prof["fields"]],
        templates=[{"name": prof["template"], "qfmt": prof["qfmt"], "afmt": prof["afmt"]}],
        css=CSS,
    )
    deck = genanki.Deck(prof["deck_id"], prof["deck_name"])
    for card in cards:
        deck.add_note(genanki.Note(model=model, fields=card_fields(card, lang),
                                   tags=anki_tags(card), guid=note_guid(card, lang)))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck).write_to_file(str(path))
    return len(cards)
