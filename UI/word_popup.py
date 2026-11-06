"""Cuadro de texto que detecta la palabra bajo el mouse (hover y clic) y ventanita con el diccionario."""
import html
import re

from urllib.parse import quote, unquote

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QTextCursor, QTextDocument)
from PyQt5.QtWidgets import QApplication, QFrame, QTextBrowser, QTextEdit, QVBoxLayout

import Translator.languages as languages
from DB.dictionary import POS_LABEL
from DB.levels_ja import chip as chip_for

_L = r"[^\W\d_]"   # una letra (Unicode)
_WORD_RE = re.compile(rf"{_L}+(?:['’]{_L}+)*")           # palabra, con apóstrofos: don't
_COMPOUND_RE = re.compile(rf"{_L}+(?:['’-]{_L}+)*")      # compuesto con guiones: well-known

MAX_DEFINITION = 200   # caracteres; las definiciones más largas se cortan con "…"
# Tamaños (px) de la ventanita: lo importante (palabra, lectura, traducción) va grande.
LEMMA_PX = 40          # palabra (escrituras con alfabeto)
LEMMA_CJK_PX = 60      # kanji: mucho más grande, para leerlo de un vistazo
READING_PX = 28
SUB_LEMMA_PX = 28      # segunda entrada y siguientes (otra lectura, otra palabra)
SUB_READING_PX = 18
MEANING_PX = 17        # traducción al español (inglés) o significado (japonés)
BODY_PX = 13           # definiciones en inglés, notas, ejemplos
SENSE_LIMIT = 4        # significados por entrada antes de «▸ Más significados»
MAX_PILLS = 5          # etiquetas visibles antes del botón «+N»
MARK_COLOR = QColor(46, 160, 67, 80)    # fondo de las palabras ya guardadas
PENDING_COLOR = QColor(235, 140, 30, 90)   # fondo de las palabras sin significado (pendientes)
CHIP_H = 22            # alto (px) de cada etiqueta de nivel o frecuencia
CHIP_GAP = 6           # separación vertical entre filas de etiquetas (va dentro de la imagen, transparente)
CHIP_W = 78            # ancho de cada etiqueta
MORE_W = 40            # ancho del botón «+N»
# Paleta oscura: la misma que el panel del modo en vivo.
BG, BORDER = "#12141a", "#2f3a4d"
FG, MUTED, POS = "#d6f1ff", "#7d8899", "#e0a35a"
READING_COLOR, LINE_COLOR = "#6fd3ff", "#2c3444"
LINK = "#9fb4d4"
GRAM, GRAM_LABEL, GRAM_TEXT, GRAM_BG = "#c9a6ff", "#a98be0", "#e3dcf5", "#221b36"
SAVED_BADGE = f"<p style='margin:0 0 6px 0; color:#6fe39a'><b>✓ Ya guardada en tu lista</b></p>"


def find_word_latin(text: str, index: int):
    """Palabra que contiene la posición `index` de `text`.

    Devuelve (inicio, fin, palabra, compuesto) o None. `compuesto` es la palabra con
    guiones que la contiene ("well-known") o "" si no hay.
    """
    for m in _WORD_RE.finditer(text):
        if m.start() <= index <= m.end():
            compound = ""
            for c in _COMPOUND_RE.finditer(text):
                if c.start() <= m.start() and m.end() <= c.end():
                    if "-" in c.group():
                        compound = c.group()
                    break
            return m.start(), m.end(), m.group(), compound
    return None


def iter_words_latin(text: str):
    """Todas las palabras de `text`: (inicio, fin, palabra, compuesto), como find_word."""
    compounds = [(c.start(), c.end(), c.group())
                 for c in _COMPOUND_RE.finditer(text) if "-" in c.group()]
    for m in _WORD_RE.finditer(text):
        compound = next((g for a, b, g in compounds if a <= m.start() and m.end() <= b), "")
        yield m.start(), m.end(), m.group(), compound


def find_word(text: str, index: int):
    """Como find_word_latin, pero con las reglas del idioma activo (languages.active())."""
    return languages.active().find_word(text, index)


def iter_words(text: str):
    """Como iter_words_latin, pero con las reglas del idioma activo."""
    return languages.active().iter_words(text)


_BASE = {n: globals()[n] for n in ("LEMMA_PX", "LEMMA_CJK_PX", "READING_PX", "SUB_LEMMA_PX", "SUB_READING_PX", "MEANING_PX",
                                   "BODY_PX", "CHIP_H", "CHIP_GAP", "CHIP_W", "MORE_W")}
_BASE_WIDTH, _BASE_HEIGHT = 520, 600
TEXT_SCALE = 1.0


def set_text_scale(percent: int) -> float:
    """Tamaño de la letra de la ventanita (70–200 %): escala todos los tamaños, las etiquetas y el ancho."""
    global TEXT_SCALE
    TEXT_SCALE = max(0.7, min(2.0, int(percent) / 100.0))
    for name, base in _BASE.items():
        globals()[name] = max(1, int(round(base * TEXT_SCALE)))
    return TEXT_SCALE


def chip_width(text: str) -> int:
    """Ancho (px) de las etiquetas: todas iguales, como las de tipo de Pokémon."""
    return CHIP_W


def _pill_src(color: str, text: str, width: int) -> str:
    return "pill:" + quote(f"{color}|{text}|{width}", safe="")


def _chips_html(tags, open_: bool = False) -> str:
    """Etiquetas como imágenes «píldora» (se dibujan en _PillBrowser.loadResource), en una fila. Si hay más de
    MAX_PILLS, el resto queda tras un botoncito «+N» (enlace «tags:») que las muestra todas."""
    chips = [c for c in (chip_for(t) for t in tags or []) if c is not None]
    shown = chips if open_ else chips[:MAX_PILLS]
    out = [f"<img src='{_pill_src(color, text, CHIP_W)}' width='{CHIP_W}' height='{CHIP_H + CHIP_GAP}'>" for text, color in shown]
    if len(chips) > MAX_PILLS:
        label = "▴" if open_ else f"+{len(chips) - MAX_PILLS}"
        out.append(f"<a href='tags:toggle' style='text-decoration:none'><img src='{_pill_src('#4a5568', label, MORE_W)}' "
                   f"width='{MORE_W}' height='{CHIP_H + CHIP_GAP}'></a>")
    return " ".join(out)


def _rule() -> str:
    return (f"<div style='font-size:5px'>&nbsp;</div><table width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td bgcolor='{LINE_COLOR}' style='font-size:1px'><span style='font-size:1px'>&nbsp;</span></td></tr></table>"
            f"<div style='font-size:5px'>&nbsp;</div>")


_PILLS = {}


def pill_image(text: str, color: str, width: int, height: int = None, gap: int = None) -> QImage:
    """Píldora de color con el texto en blanco, al doble de resolución (se ve nítida al escalarla).
    Debajo lleva `gap` px transparentes: así las filas de etiquetas no quedan pegadas."""
    height = CHIP_H if height is None else height
    gap = CHIP_GAP if gap is None else gap
    key = (text, color, width, height, gap)
    if key in _PILLS:
        return _PILLS[key]
    k = 2
    img = QImage(width * k, (height + gap) * k, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    base = QColor(color)
    rect = QRectF(1, 1, width * k - 2, height * k - 2)
    p.setBrush(base)
    p.setPen(QPen(base.darker(125), 2))
    p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    font = QFont()
    font.setBold(True)
    size = int(height * k * 0.52)
    font.setPixelSize(size)
    while size > 8 and QFontMetrics(font).horizontalAdvance(text) > rect.width() - 14 * k:
        size -= 1                                     # texto largo: se achica para caber
        font.setPixelSize(size)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(rect, Qt.AlignCenter, text)
    p.end()
    _PILLS[key] = img
    return img


class _PillBrowser(QTextBrowser):
    """QTextBrowser que dibuja las imágenes «pill:…» del HTML (etiquetas de nivel y frecuencia)."""

    def loadResource(self, rtype, url):
        if url.scheme() == "pill":
            color, _sep, rest = unquote(bytes(url.toEncoded()).decode("ascii")[5:]).partition("|")
            text, _sep, width = rest.rpartition("|")
            return pill_image(text, color, int(width) if width.isdigit() else CHIP_W)
        return super().loadResource(rtype, url)


def _is_cjk(text: str) -> bool:
    return any("\u3040" <= ch <= "\u9fff" for ch in text)


def _grammar_html(entry) -> str:
    """Forma gramatical: una caja morada con el patrón, su significado, nivel y un ejemplo."""
    esc = html.escape
    s = entry.senses[0] if entry.senses else None
    inner = (f"<span style='font-size:11px; color:{GRAM_LABEL}; letter-spacing:1px'><b>GRAMÁTICA</b></span><br>"
             f"<span style='font-size:{READING_PX - 4}px; font-weight:600; color:{GRAM}'>{esc(entry.lemma)}</span>")
    if s is not None and s.definition:
        inner += f" <span style='font-size:{MEANING_PX - 2}px; color:{GRAM_TEXT}'>&nbsp;{esc(s.definition)}</span>"
    if entry.note:
        inner += f"<br><span style='font-size:{BODY_PX - 1}px; color:{MUTED}'>{esc(entry.note)}</span>"
    if s is not None and s.example:
        jp, _sep, en = s.example.partition(" — ")
        inner += f"<br><span style='font-size:{BODY_PX + 1}px; color:{GRAM_TEXT}'>{esc(jp)}</span>"
        if en:
            inner += f"<br><span style='font-size:{BODY_PX - 1}px; color:{MUTED}'><i>{esc(en)}</i></span>"
    return (f"<table width='100%' cellspacing='0' cellpadding='9' style='margin:4px 0'><tr><td bgcolor='{GRAM_BG}'>{inner}</td></tr></table>"
            "<div style='font-size:4px'>&nbsp;</div>")


def hidden_senses(result) -> int:
    """Significados que quedan escondidos mientras no se pulse «▸ Más significados»."""
    return sum(max(0, len(e.senses) - SENSE_LIMIT) for e in result.entries if e.kind != "grammar")


def copy_text(result) -> str:
    """Texto que copia «⧉ Copiar»: la palabra, su lectura y los primeros significados."""
    lines = []
    for e in result.entries:
        if e.kind == "grammar":
            continue
        head = e.lemma + (f" ({e.reading})" if e.reading else "")
        meanings = []
        for sense in e.senses[:SENSE_LIMIT]:
            meanings.append(", ".join(sense.spanish[:4]) if sense.spanish else sense.definition)
        lines.append(head + (": " + "; ".join(meanings) if meanings else ""))
        if len(lines) >= 2:
            break
    return "\n".join(lines) or result.query


def render_html(result, saved: bool = False, tags_open: bool = False, senses_open: bool = False, copied: bool = False) -> str:
    """HTML sencillo (subconjunto que entiende QTextDocument) para un LookupResult, con la paleta oscura."""
    esc = html.escape
    parts = [SAVED_BADGE] if saved else []
    previous = None
    shown = set()                                      # (palabra, etiquetas) que ya se mostraron
    first_word = True
    grammar_parts = []                                 # la gramática va tras los significados, antes del pie
    for entry in result.entries:
        if entry.kind == "grammar":
            grammar_parts.append(_grammar_html(entry))
            previous = entry
            continue
        if previous is not None and previous.kind == "function" and previous.lemma == entry.lemma:
            head_html = f"<p style='margin:6px 0 3px 0'><span style='color:{MUTED}; font-size:{BODY_PX}px'>{esc(entry.lemma)} — otros significados</span></p>"
        else:
            cjk = _is_cjk(entry.lemma)
            big = first_word
            lemma_px = (LEMMA_CJK_PX if cjk else LEMMA_PX) if big else SUB_LEMMA_PX
            reading_px = READING_PX if big else SUB_READING_PX
            head = f"<span style='font-size:{lemma_px}px; font-weight:{500 if cjk else 600}; color:#ffffff'>{esc(entry.lemma)}</span>"
            if entry.reading:
                head += f" &nbsp;<span style='font-size:{reading_px}px; color:{READING_COLOR}'>{esc(entry.reading)}</span>"
            if entry.lemma.lower() != result.query.lower():
                head += f" &nbsp;<span style='color:{MUTED}; font-size:{BODY_PX + 2}px'>← {esc(result.query)}</span>"
            head_html = f"<p style='margin:{2 if big else 8}px 0 2px 0'>{head}</p>"
            first_word = False
        previous = entry
        sig = (entry.lemma, tuple(entry.tags or ()))
        chips = _chips_html(entry.tags, tags_open) if sig not in shown else ""     # la misma palabra: etiquetas una vez
        if chips:
            shown.add(sig)
            head_html += f"<p style='margin:0 0 2px 0'>{chips}</p>"
        parts.append(head_html)
        parts.append(_rule())

        if entry.note:
            parts.append(f"<p style='margin:0 0 3px 0; color:{MUTED}; font-size:{BODY_PX}px'><i>{esc(entry.note)}</i></p>")
        senses = entry.senses if senses_open else entry.senses[:SENSE_LIMIT]
        for n, s in enumerate(senses, 1):
            definition = s.definition if len(s.definition) <= MAX_DEFINITION else \
                s.definition[:MAX_DEFINITION].rsplit(" ", 1)[0] + "…"
            label = (f"<span style='color:{MUTED}'>{n}.</span> <i style='color:{POS}; font-size:{BODY_PX}px'>"
                     f"{POS_LABEL.get(s.pos, s.pos)}</i>")
            if s.spanish:      # inglés/francés: la traducción va grande y la definición, más pequeña
                lines = [f"{label} <span style='font-size:{MEANING_PX + 1}px; font-weight:600; color:{FG}'>"
                         f"{esc(', '.join(s.spanish[:4]))}</span>"]
                if definition:
                    lines.append(f"<span style='color:{MUTED}; font-size:{BODY_PX}px'>{esc(definition)}</span>")
            else:              # japonés: el significado en inglés es lo principal
                lines = [f"{label} <span style='font-size:{MEANING_PX + 1}px; font-weight:600; color:{FG}'>{esc(definition)}</span>"]
            if entry.kind == "function" and s.example:
                lines.append(f"<span style='color:#9aa7bb; font-size:{BODY_PX}px'><i>{esc(s.example)}</i></span>")
            if s.synonyms:
                lines.append(f"<span style='color:#9aa7bb; font-size:{BODY_PX}px'>≈ {esc(', '.join(s.synonyms[:5]))}</span>")
            if s.antonyms:
                lines.append(f"<span style='color:#e08a8a; font-size:{BODY_PX}px'>≠ {esc(', '.join(s.antonyms[:4]))}</span>")
            parts.append("<p style='margin:0 0 7px 0'>" + "<br>".join(lines) + "</p>")
        extra = entry.total - len(entry.senses)           # lo que el diccionario recortó (lo escondido por el límite va en el pie)
        if extra > 0:
            parts.append(f"<p style='margin:0 0 6px 0; color:{MUTED}; font-size:{BODY_PX}px'>+{extra} significado(s) más</p>")
    parts.extend(grammar_parts)
    hidden = hidden_senses(result)
    acts = []
    if saved:
        acts.append(f"<span style='color:#6fe39a'>✓ Guardada</span>")
    else:
        acts.append(f"<a href='act:save' style='color:{LINK}; text-decoration:none'>＋ Guardar</a>")
    acts.append(f"<span style='color:#6fe39a'>✓ Copiado</span>" if copied else
                f"<a href='act:copy' style='color:{LINK}; text-decoration:none'>⧉ Copiar</a>")
    if hidden or senses_open and any(len(e.senses) > SENSE_LIMIT for e in result.entries):
        acts.append(f"<a href='act:more' style='color:{LINK}; text-decoration:none'>"
                    + ("▴ Menos significados" if senses_open else f"▸ Más significados (+{hidden})") + "</a>")
    parts.append(_rule())
    parts.append(f"<p style='margin:2px 0 0 0; font-size:{BODY_PX}px'>" + " &nbsp;&nbsp;·&nbsp;&nbsp; ".join(acts) + "</p>")
    return "".join(parts)


class HoverTextEdit(QTextEdit):
    """QTextEdit que avisa cuando el mouse se queda quieto sobre una palabra.

    word_hovered(palabra, compuesto, posición_global): tras `delay_ms` sobre la misma palabra.
    hover_ended(): hay que ocultar la ventanita (el mouse cambió de palabra o salió del texto,
    hubo clic, desplazamiento o cambió el contenido).
    """

    word_hovered = pyqtSignal(str, str, QPoint)
    # Justo antes de word_hovered: el texto (línea) donde está la palabra y su posición en él.
    # Lo usa la capa de gramática japonesa para ver las piezas vecinas.
    word_context = pyqtSignal(str, int)
    hover_ended = pyqtSignal()
    # Clic sobre una palabra (sin arrastrar): palabra, compuesto y su posición en el texto.
    word_clicked = pyqtSignal(str, str, int)
    # Rueda del mouse mientras la ventanita está visible: la desplaza en vez de mover el texto.
    wheel_moved = pyqtSignal(int)

    def __init__(self, delay_ms: int = 350, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._emit_pending)
        self._key = None        # (inicio, fin) absolutos de la palabra bajo el mouse
        self._pending = None    # (palabra, compuesto, posición global)
        self._pending_ctx = None  # (texto de la línea, posición de la palabra en ella)
        self._shown = False     # ¿ya se emitió word_hovered para la palabra actual?
        self._press = None      # dónde se pulsó el botón izquierdo (para distinguir clic de arrastre)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._end())
        self.textChanged.connect(self._end)

    def _word_at(self, pos: QPoint):
        """((inicio, fin), palabra, compuesto) bajo `pos` (coordenadas del viewport) o None."""
        point = QPointF(pos.x() + self.horizontalScrollBar().value(),
                        pos.y() + self.verticalScrollBar().value())
        idx = self.document().documentLayout().hitTest(point, Qt.ExactHit)
        if idx < 0:                      # el mouse no está sobre texto
            return None
        block = self.document().findBlock(idx)
        i = idx - block.position()
        hit = find_word(block.text(), i)
        if hit is None:
            return None
        start, end, word, compound = hit
        if not start <= i < end:         # sobre el espacio o signo pegado a la palabra
            return None
        base = block.position()
        return (base + start, base + end), word, compound

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        hit = self._word_at(e.pos())
        key = hit[0] if hit else None
        if key == self._key:
            return
        self._end()
        self._key = key
        if hit:
            self._pending = (hit[1], hit[2], e.globalPos())
            block = self.document().findBlock(hit[0][0])
            self._pending_ctx = (block.text(), hit[0][0] - block.position())
            self._timer.start()

    def _emit_pending(self):
        if self._pending:
            self._shown = True
            ctx = getattr(self, "_pending_ctx", None)
            if ctx:
                self.word_context.emit(*ctx)
            self.word_hovered.emit(*self._pending)

    def _end(self):
        self._timer.stop()
        self._key = None
        self._pending = None
        self._pending_ctx = None
        if self._shown:
            self._shown = False
            self.hover_ended.emit()

    def wheelEvent(self, e):
        if self._shown:
            self.wheel_moved.emit(e.angleDelta().y())
            e.accept()
            return
        super().wheelEvent(e)

    def viewportEvent(self, e):
        if e.type() == QEvent.Leave:
            self._end()
        return super().viewportEvent(e)

    def mousePressEvent(self, e):
        self._end()
        self._press = e.pos() if e.button() == Qt.LeftButton else None
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        press, self._press = self._press, None
        if press is None or e.button() != Qt.LeftButton:
            return
        if (e.pos() - press).manhattanLength() > 4 or self.textCursor().hasSelection():
            return                       # fue un arrastre para seleccionar texto, no un clic
        hit = self._word_at(e.pos())
        if hit:
            (start, _end), word, compound = hit
            self.word_clicked.emit(word, compound, start)

    def set_marks(self, ranges, pending=()):
        """Resalta los rangos (inicio, fin) del texto: guardadas (verde) y pendientes (naranja)."""
        selections = []
        for (start, end), color in [(r, MARK_COLOR) for r in ranges] + [(r, PENDING_COLOR) for r in pending]:
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format.setBackground(color)
            selections.append(sel)
        self.setExtraSelections(selections)


class WordPopup(QFrame):
    """Ventanita flotante con el resultado. No roba el foco; se puede entrar con el mouse y
    desplazar con la rueda para ver todos los significados."""

    WIDTH = _BASE_WIDTH
    MAX_HEIGHT = _BASE_HEIGHT
    entered = pyqtSignal()      # el mouse entró en la ventanita
    left = pyqtSignal()         # el mouse salió
    save_requested = pyqtSignal()      # «＋ Guardar» (la ventana de palabras guarda la que se está mostrando)
    _anchor = None              # (QRect global de la palabra, arriba?) → el popup se pega a su vértice

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("wordPopup")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # Colores fijos (paleta oscura, la del panel del modo en vivo): se lee igual con cualquier tema del sistema.
        self.setStyleSheet(
            f"QFrame#wordPopup {{ background:{BG}; border:1px solid {BORDER}; border-radius:12px; }}"
            f"QTextBrowser {{ background:transparent; color:{FG}; border:none; }}"
            "QScrollBar:vertical { background:transparent; width:8px; margin:6px 2px; }"
            "QScrollBar::handle:vertical { background:#3a475c; border-radius:4px; min-height:24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }")
        self._result = None                 # lo que se muestra (para «Copiar» y para volver a dibujar)
        self._saved = False
        self._tags_open = self._senses_open = self._copied = False
        self._last_pos = QPoint()
        self._view = _PillBrowser()
        self._view.setFrameShape(QFrame.NoFrame)
        self._view.setOpenLinks(False)
        self._view.anchorClicked.connect(self._on_anchor)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._view.document().setDocumentMargin(16)
        font = self._view.font()
        font.setPixelSize(BODY_PX)
        self._view.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self._view)

    def set_scale(self, percent: int):
        """Cambia el tamaño de la letra (y con él el ancho de la ventanita) y vuelve a dibujar lo que se muestra."""
        set_text_scale(percent)
        self.WIDTH = int(round(_BASE_WIDTH * TEXT_SCALE))
        self.MAX_HEIGHT = int(round(_BASE_HEIGHT * max(1.0, TEXT_SCALE)))
        font = self._view.font()
        font.setPixelSize(BODY_PX)
        self._view.setFont(font)
        if self.isVisible() and self._result is not None:
            self._render()

    def plain_text(self) -> str:
        return self._view.toPlainText()

    def set_anchor(self, rect=None, above: bool = False):
        """Pega el popup al rectángulo `rect` (global) de la palabra: arriba o abajo de él, alineado a su
        borde izquierdo, para no estorbar el recorrido del mouse. `None` vuelve a ubicarlo junto al cursor."""
        self._anchor = None if rect is None else (QRect(rect), bool(above))

    @property
    def scrollable(self) -> bool:
        return self._view.verticalScrollBar().maximum() > 0

    def show_result(self, result, global_pos: QPoint, saved: bool = False):
        if result is not self._result:                    # otra palabra: todo plegado otra vez
            self._tags_open = self._senses_open = self._copied = False
        self._result, self._saved, self._last_pos = result, bool(saved), QPoint(global_pos)
        self._render(global_pos)

    def show_message(self, text: str, global_pos: QPoint, saved: bool = False):
        self._result = None
        badge = SAVED_BADGE if saved else ""
        self._show(f"{badge}<span style='color:{MUTED}'>{html.escape(text)}</span>", global_pos)

    def _render(self, pos: QPoint = None):
        if self._result is None:
            return
        self._show(render_html(self._result, self._saved, self._tags_open, self._senses_open, self._copied),
                   pos if pos is not None else self._last_pos, keep_scroll=pos is None)

    def mark_saved(self, saved: bool = True):
        """La palabra mostrada se guardó (o dejó de estarlo): cambia el badge y el pie."""
        if self._result is not None and self.isVisible():
            self._saved = bool(saved)
            self._render()

    def _on_anchor(self, url):
        link = url.toString()
        if link == "tags:toggle":
            self._tags_open = not self._tags_open
        elif link == "act:more":
            self._senses_open = not self._senses_open
        elif link == "act:copy" and self._result is not None:
            QApplication.clipboard().setText(copy_text(self._result))
            self._copied = True
            QTimer.singleShot(1500, self._end_copied)
        elif link == "act:save":
            self.save_requested.emit()
            return
        else:
            return
        self._render()

    def _end_copied(self):
        if self._copied:
            self._copied = False
            self._render()

    def scroll_by(self, delta: int):
        """Desplaza el contenido con un delta de rueda (120 por muesca; positivo = hacia arriba)."""
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.value() - int(delta / 120 * 48))

    def _measure(self, markup: str) -> int:
        self._view.setHtml(markup)
        doc = self._view.document()
        doc.setTextWidth(self.WIDTH - 2)
        return int(doc.size().height()) + 6

    def _show(self, markup: str, global_pos: QPoint, keep_scroll: bool = False):
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        limit = min(self.MAX_HEIGHT, int(area.height() * 0.6))
        height = self._measure(markup)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)       # cabe: sin barra (así lo medido es exacto)
        if height > limit:       # no cabe: se desplaza, y se avisa
            self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            markup += f"<p style='color:{MUTED}; font-size:{BODY_PX}px'>↕ Rueda del mouse para ver más</p>"
            self._measure(markup)
            height = limit
        before = self._view.verticalScrollBar().value()
        self._view.setFixedSize(self.WIDTH, height)
        self.setFixedSize(self.WIDTH + 2, height + 2)       # +2: borde de 1 px
        self._view.verticalScrollBar().setValue(before if keep_scroll else 0)
        if keep_scroll and self.isVisible():        # «+», «Más significados», «Copiar»…: la ventanita no se mueve, solo crece hacia abajo
            x = max(area.left(), min(self.x(), area.right() - self.width()))
            y = max(area.top(), min(self.y(), area.bottom() - self.height()))
        elif self._anchor is not None:
            rect, above = self._anchor
            x = rect.left()
            y = rect.top() - self.height() if above else rect.bottom() + 1
            if y < area.top():                      # no cabe arriba: abajo (y al revés)
                y = rect.bottom() + 1
            elif y + self.height() > area.bottom() + 1 and not above:
                y = rect.top() - self.height()
            x = max(area.left(), min(x, area.right() - self.width()))
            y = max(area.top(), min(y, area.bottom() - self.height()))
        else:
            x, y = global_pos.x() + 10, global_pos.y() + 14     # cerca del cursor: se puede entrar con poco movimiento
            if x + self.width() > area.right():
                x = max(area.left(), global_pos.x() - self.width() - 10)
            if y + self.height() > area.bottom():
                y = max(area.top(), area.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.raise_()

    def enterEvent(self, e):
        self.entered.emit()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.left.emit()
        super().leaveEvent(e)
