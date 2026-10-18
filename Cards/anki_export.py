"""Exportación de tarjetas a Anki: archivo de texto (se importa desde Anki) y paquete .apkg.

Campos de cada nota, en este orden:
    Word | Meaning | Sentence | SentenceTranslation | Extra

- Archivo de texto (.txt): no necesita nada más. En Anki: Archivo > Importar, y en el cuadro
  elige tu tipo de nota (por ejemplo el de tu mazo de japonés) y asigna cada columna a un
  campo. Si vuelves a importar, Anki actualiza las notas cuya primera columna (la palabra)
  ya existe en lugar de duplicarlas.
- Paquete (.apkg): necesita `pip install genanki`. Crea el tipo de nota «OCR Translator –
  Vocabulario» con su plantilla (anverso: la palabra; reverso: significado, frase en inglés
  con la palabra en negrita y su traducción). Cada nota lleva un identificador estable, así
  que importar de nuevo actualiza las notas existentes.
"""
import csv
import hashlib
import html
import re
from pathlib import Path

TAG = "OCR_Translator"
FIELDS = ["Word", "Meaning", "Sentence", "SentenceTranslation", "Extra"]

# Identificadores fijos: Anki reconoce el mismo tipo de nota y el mismo mazo en cada importación.
MODEL_ID = 1830629467
DECK_ID = 1830629468
MODEL_NAME = "OCR Translator – Vocabulario"
DECK_NAME = "OCR Translator"

CSS = """
.card { font-family: Arial, sans-serif; font-size: 20px; text-align: center; }
.word { font-size: 34px; font-weight: bold; }
.meaning { text-align: left; display: inline-block; margin: 6px 0; }
.sentence { margin-top: 14px; font-size: 22px; }
.sentence b { color: #d9534f; }
.translation { margin-top: 4px; color: #4a7c59; }
.extra { margin-top: 12px; font-size: 16px; color: #888; }
"""
QFMT = '<div class="word">{{Word}}</div>'
AFMT = (
    '{{FrontSide}}<hr id="answer">'
    '<div class="meaning">{{Meaning}}</div>'
    '{{#Sentence}}<div class="sentence">{{Sentence}}</div>'
    '<div class="translation">{{SentenceTranslation}}</div>{{/Sentence}}'
    '{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}'
)


def _esc(text: str) -> str:
    """Texto plano -> HTML: escapa <, > y &, y convierte los saltos de línea en <br>."""
    text = text.replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(text, quote=False).replace("\n", "<br>")


def _html(text: str) -> str:
    return _esc(text.strip())


def sentence_html(card) -> str:
    """Frase con la palabra en negrita (primera aparición de la palabra completa)."""
    text, surface = card.sentence.strip(), card.surface.strip()
    if surface:
        m = re.search(rf"(?<!\w){re.escape(surface)}(?!\w)", text, re.IGNORECASE)
        if m:
            return _esc(text[:m.start()]) + "<b>" + _esc(m.group()) + "</b>" + _esc(text[m.end():])
    return _esc(text)


def card_fields(card) -> list:
    """Los cinco campos de la nota, ya en HTML."""
    return [_html(card.word), _html(card.meaning), sentence_html(card),
            _html(card.sentence_es), _html(card.extra)]


def note_guid(card) -> str:
    """Identificador estable de la nota (depende solo de la clave de la tarjeta)."""
    return hashlib.sha1(f"ocr-translator:{card.key}".encode("utf-8")).hexdigest()[:10]


def export_tsv(cards, path) -> int:
    """Escribe un archivo de texto para importar en Anki. Devuelve cuántas tarjetas escribió."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cards = list(cards)
    with open(path, "w", encoding="utf-8", newline="") as f:
        # Cabeceras de Anki (2.1.54 o posterior): separador, HTML permitido y columna de etiquetas.
        f.write("#separator:tab\n#html:true\n#tags column:6\n")
        writer = csv.writer(f, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        for card in cards:
            writer.writerow(card_fields(card) + [TAG])
    return len(cards)


def export_apkg(cards, path) -> int:
    """Escribe un paquete .apkg. Lanza ImportError si falta genanki."""
    import genanki  # import tardío: es opcional

    model = genanki.Model(
        MODEL_ID, MODEL_NAME,
        fields=[{"name": name} for name in FIELDS],
        templates=[{"name": "Palabra → significado", "qfmt": QFMT, "afmt": AFMT}],
        css=CSS,
    )
    deck = genanki.Deck(DECK_ID, DECK_NAME)
    cards = list(cards)
    for card in cards:
        deck.add_note(genanki.Note(model=model, fields=card_fields(card),
                                   tags=[TAG], guid=note_guid(card)))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck).write_to_file(str(path))
    return len(cards)