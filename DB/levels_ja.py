"""Nivel JLPT y frecuencia de una palabra japonesa (extra opcional junto al diccionario).

Lee data/levels_ja.db (lo crea build_levels_ja.py). Si no existe, todo sigue igual, sin etiquetas.

    levels = Levels()
    levels.tags("勝つ", "かつ")   ->  ["JLPT N3", "Anime #1 234", ...]
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "levels_ja.db"

# Nombre en pantalla de cada fuente de frecuencia, en el orden en que se muestran.
FREQ_LABELS = {"game": "Videojuegos", "anime": "Anime", "vn": "Novelas visuales", "drama": "Dramas",
               "news": "Noticias", "global": "General"}
BANDS = (1000, 2000, 5000, 10000, 20000, 50000)
# Bandas más finas para las etiquetas de Anki (freq::anime::top_2000).
FINE_BANDS = (500, 1000, 1500, 2000, 3000, 4000, 5000, 7500, 10000, 15000, 20000, 30000, 40000, 50000)
# Texto corto y color de cada «chip» del popup.
FREQ_SHORT = {"game": "Juegos", "anime": "Anime", "vn": "Nov. vis.", "drama": "Dramas",
              "news": "Noticias", "global": "General"}
FREQ_COLORS = {"game": "#ef6c00", "anime": "#d81b60", "vn": "#6a4bb5", "drama": "#7b5e50",
               "news": "#455a64", "global": "#607d8b"}
JLPT_COLORS = {5: "#2e9d3a", 4: "#00897b", 3: "#1e78d2", 2: "#8e24aa", 1: "#d32f2f"}


def band(rank: int) -> str:
    """«top 2 000» para un puesto de frecuencia; «más de 50 000» si pasa de la última banda."""
    for b in BANDS:
        if rank <= b:
            return f"top {b:,}".replace(",", " ")
    return f"más de {BANDS[-1]:,}".replace(",", " ")


def fine_band(rank: int) -> str:
    """Banda fina para las etiquetas de Anki: «top_2000» (o «mas_de_50000»)."""
    for b in FINE_BANDS:
        if rank <= b:
            return f"top_{b}"
    return f"mas_de_{FINE_BANDS[-1]}"


def number(n: int) -> str:
    """1234 -> «1 234»."""
    return f"{n:,}".replace(",", " ")


def chip(tag: str):
    """(texto corto, color) del chip de una etiqueta del popup, o None si no se reconoce."""
    if tag.startswith("JLPT N") and tag[6:].isdigit():
        return tag, JLPT_COLORS.get(int(tag[6:]), "#607d8b")
    for kind, label in FREQ_LABELS.items():
        if tag.startswith(label + " #"):
            return f"{FREQ_SHORT[kind]} #{tag[len(label) + 2:]}", FREQ_COLORS[kind]
    return None


def to_hiragana(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


class Levels:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path).resolve()
        self._con = None
        self._cache = {}
        if self.path.exists():
            self._con = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, check_same_thread=False)

    @property
    def available(self) -> bool:
        return self._con is not None

    def info(self, word: str, reading: str = "") -> dict:
        """{'jlpt': 3, 'game': 1200, ...} para esa palabra con esa lectura (la mejor de cada fuente)."""
        key = (word, reading)
        if key in self._cache:
            return self._cache[key]
        out = {}
        if self._con is not None and word:
            want = to_hiragana(reading) if reading else ""
            for r_word_reading, kind, value in self._con.execute(
                    "SELECT reading, kind, value FROM info WHERE word=?", (word,)):
                if want and r_word_reading and to_hiragana(r_word_reading) != want:
                    continue                                    # otra lectura de la misma escritura
                if kind == "jlpt":
                    out["jlpt"] = max(out.get("jlpt", 0), value)    # varias: la más fácil (N5 > N1)
                else:
                    out[kind] = min(out.get(kind, value), value)    # varias: el puesto más alto
        self._cache[key] = out
        return out

    def tags(self, word: str, reading: str = "") -> list:
        """Etiquetas legibles: «JLPT N3», «Anime #1 234» (el puesto exacto en esa lista)."""
        info = self.info(word, reading)
        out = []
        if "jlpt" in info:
            out.append(f"JLPT N{info['jlpt']}")
        for kind, label in FREQ_LABELS.items():
            if kind in info:
                out.append(f"{label} #{number(info[kind])}")
        return out

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None
