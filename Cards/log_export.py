"""Exportación del registro de frases: CSV (se abre en Excel o Calc) y texto legible.

    export_csv(entries, "frases.csv")     # una fila por captura
    export_txt(entries, "frases.txt")     # bloques fecha / original / traducción

`entries` son LogEntry de phrase_log. No usa Qt: se puede probar sola.
"""
import csv
from pathlib import Path

HEADER = ["Fecha", "Idioma", "Original", "Traducción", "Líneas unidas", "Confianza OCR"]
LANG_NAME = {"en": "inglés", "ja": "japonés"}


def _lang(entry) -> str:
    return LANG_NAME.get(entry.lang, entry.lang)


def export_csv(entries, path) -> int:
    """CSV con BOM UTF-8 (Excel reconoce así los acentos y el japonés). La más vieja primero."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(entries, key=lambda e: e.id)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writerow(HEADER)
        for e in entries:
            writer.writerow([e.ts, _lang(e), e.original, e.translation,
                             "sí" if e.merged else "no", f"{e.score:.0%}"])
    return len(entries)


def export_txt(entries, path) -> int:
    """Texto legible: un bloque por captura, la más vieja primero."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(entries, key=lambda e: e.id)
    blocks = []
    for n, e in enumerate(entries, 1):
        blocks.append(
            f"#{n}  {e.ts}  [{_lang(e)}]  confianza {e.score:.0%}\n"
            f"Original:\n{e.original.strip()}\n"
            f"Traducción:\n{e.translation.strip() or '(sin traducción)'}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return len(entries)
