"""Cuadro de texto que detecta la palabra bajo el mouse (hover y clic) y ventanita con el diccionario."""
import html
import re
from urllib.parse import quote, unquote

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, QUrl, pyqtSignal
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
LEMMA_PX = 28
READING_PX = 20
MEANING_PX = 17        # traducción al español (inglés) o significado (japonés)
BODY_PX = 13           # definiciones en inglés, notas, ejemplos
MARK_COLOR = QColor(46, 160, 67, 80)    # fondo de las palabras ya guardadas
PENDING_COLOR = QColor(235, 140, 30, 90)   # fondo de las palabras sin significado (pendientes)
CHIP_H = 22            # alto (px) de cada etiqueta de nivel o frecuencia
CHIP_GAP = 6           # separación vertical entre filas de etiquetas (va dentro de la imagen, transparente)
CHIP_W = 104           # ancho de cada etiqueta
CHIP_COL = 236         # ancho del bloque de etiquetas, a la derecha de la palabra (dos por fila)
SAVED_BADGE = "<p style='margin:0 0 4px 0; color:#1b6b2f'><b>✓ Ya guardada en tu lista</b></p>"


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


def chip_width(text: str) -> int:
    """Ancho (px) de las etiquetas: todas iguales, como las de tipo de Pokémon."""
    return CHIP_W


def _chips_html(tags) -> str:
    """Etiquetas como imágenes «píldora» (se dibujan en _PillBrowser.loadResource), en filas de dos
    con aire entre ellas."""
    out = []
    for tag in tags or []:
        c = chip_for(tag)
        if c is None:
            continue
        text, color = c
        src = "pill:" + quote(f"{color}|{text}", safe="")
        out.append(f"<img src='{src}' width='{chip_width(text)}' height='{CHIP_H + CHIP_GAP}'>")
    return " &nbsp;".join(out)


_PILLS = {}


def pill_image(text: str, color: str, width: int, height: int = CHIP_H, gap: int = CHIP_GAP) -> QImage:
    """Píldora de color con el texto en blanco, al doble de resolución (se ve nítida al escalarla).
    Debajo lleva `gap` px transparentes: así las filas de etiquetas no quedan pegadas."""
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
            color, _sep, text = unquote(bytes(url.toEncoded()).decode("ascii")[5:]).partition("|")
            return pill_image(text, color, chip_width(text))
        return super().loadResource(rtype, url)


def render_html(result, saved: bool = False) -> str:
    """HTML sencillo (subconjunto que entiende QTextDocument) para un LookupResult."""
    esc = html.escape
    parts = [SAVED_BADGE] if saved else []
    previous = None
    shown = set()                                      # (palabra, etiquetas) que ya se mostraron
    for entry in result.entries:
        if entry.kind == "grammar":                    # forma gramatical: encabezado discreto, sin numerar
            parts.append(f"<p style='margin:6px 0 2px 0'><span style='font-size:{READING_PX}px; font-weight:600; "
                         f"color:#6b3fa0'>{esc(entry.lemma)}</span> <span style='color:#888; font-size:{BODY_PX}px'>"
                         f"gramática</span></p>")
            if entry.note:
                parts.append(f"<p style='margin:0 0 2px 0; color:#888; font-size:{BODY_PX}px'><i>{esc(entry.note)}</i></p>")
            for s in entry.senses:
                text = f"<span style='font-size:{MEANING_PX}px'>{esc(s.definition)}</span>"
                if s.example:
                    text += f"<br><span style='color:#555; font-size:{BODY_PX}px'><i>{esc(s.example)}</i></span>"
                parts.append(f"<p style='margin:0 0 8px 0'>{text}</p>")
            previous = entry
            continue
        if previous is not None and previous.kind == "function" and previous.lemma == entry.lemma:
            head = f"<span style='color:#888; font-size:{BODY_PX}px'>{esc(entry.lemma)} — otros significados</span>"
        else:
            head = f"<span style='font-size:{LEMMA_PX}px; font-weight:600'>{esc(entry.lemma)}</span>"
            if entry.reading:
                head += (f" <span style='font-size:{READING_PX}px; color:#1f4f8f'>"
                         f"{esc(entry.reading)}</span>")
            if entry.lemma.lower() != result.query.lower():
                head += f" <span style='color:#888; font-size:{BODY_PX}px'>← {esc(result.query)}</span>"
        previous = entry
        head_html = f"<p style='margin:6px 0 3px 0'>{head}</p>"
        sig = (entry.lemma, tuple(entry.tags or ()))
        chips = _chips_html(entry.tags) if sig not in shown else ""     # la misma palabra: etiquetas una vez
        if chips:
            shown.add(sig)
            head_html = ("<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                         f"<td valign='middle'>{head_html}</td>"
                         f"<td valign='top' align='right' width='{CHIP_COL}'>{chips}</td></tr></table>")
        parts.append(head_html)

        if entry.note:
            parts.append(f"<p style='margin:0 0 3px 0; color:#888; font-size:{BODY_PX}px'>"
                         f"<i>{esc(entry.note)}</i></p>")
        for n, s in enumerate(entry.senses, 1):
            definition = s.definition if len(s.definition) <= MAX_DEFINITION else \
                s.definition[:MAX_DEFINITION].rsplit(" ", 1)[0] + "…"
            label = (f"<b>{n}.</b> <i style='color:#8a4b1f; font-size:{BODY_PX}px'>"
                     f"{POS_LABEL.get(s.pos, s.pos)}</i>")
            if s.spanish:      # inglés: la traducción va grande y la definición en inglés, más pequeña
                lines = [f"{label} <span style='font-size:{MEANING_PX}px; font-weight:600; color:#1b6b2f'>"
                         f"{esc(', '.join(s.spanish[:4]))}</span>"]
                if definition:
                    lines.append(f"<span style='color:#555; font-size:{BODY_PX}px'>{esc(definition)}</span>")
            else:              # japonés: el significado en inglés es lo principal
                lines = [f"{label} <span style='font-size:{MEANING_PX}px'>{esc(definition)}</span>"]
            if entry.kind == "function" and s.example:
                lines.append(f"<span style='color:#555; font-size:{BODY_PX}px'><i>{esc(s.example)}</i></span>")
            if s.synonyms:
                lines.append(f"<span style='color:#444; font-size:{BODY_PX}px'>≈ {esc(', '.join(s.synonyms[:5]))}</span>")
            if s.antonyms:
                lines.append(f"<span style='color:#a02020; font-size:{BODY_PX}px'>≠ {esc(', '.join(s.antonyms[:4]))}</span>")
            parts.append("<p style='margin:0 0 6px 0'>" + "<br>".join(lines) + "</p>")
        extra = entry.total - len(entry.senses)
        if extra > 0:
            parts.append(f"<p style='margin:0 0 6px 0; color:#888; font-size:{BODY_PX}px'>"
                         f"+{extra} significado(s) más</p>")
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

    WIDTH = 440
    MAX_HEIGHT = 460
    entered = pyqtSignal()      # el mouse entró en la ventanita
    left = pyqtSignal()         # el mouse salió

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("wordPopup")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # Colores fijos: así se lee igual con el tema claro u oscuro del sistema.
        self.setStyleSheet(
            "QFrame#wordPopup { background:#fffdf6; border:1px solid #b9b192; border-radius:6px; }"
            "QTextBrowser { background:transparent; color:#222; border:none; }")
        self._view = _PillBrowser()
        self._view.setFrameShape(QFrame.NoFrame)
        self._view.setOpenLinks(False)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._view.document().setDocumentMargin(10)
        font = self._view.font()
        font.setPixelSize(BODY_PX)
        self._view.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self._view)

    def plain_text(self) -> str:
        return self._view.toPlainText()

    @property
    def scrollable(self) -> bool:
        return self._view.verticalScrollBar().maximum() > 0

    def show_result(self, result, global_pos: QPoint, saved: bool = False):
        self._show(render_html(result, saved), global_pos)

    def show_message(self, text: str, global_pos: QPoint, saved: bool = False):
        badge = SAVED_BADGE if saved else ""
        self._show(f"{badge}<span style='color:#666'>{html.escape(text)}</span>", global_pos)

    def scroll_by(self, delta: int):
        """Desplaza el contenido con un delta de rueda (120 por muesca; positivo = hacia arriba)."""
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.value() - int(delta / 120 * 48))

    def _measure(self, markup: str) -> int:
        self._view.setHtml(markup)
        doc = self._view.document()
        doc.setTextWidth(self.WIDTH - 2)
        return int(doc.size().height()) + 4

    def _show(self, markup: str, global_pos: QPoint):
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        limit = min(self.MAX_HEIGHT, int(area.height() * 0.6))
        height = self._measure(markup)
        if height > limit:       # no cabe: se desplaza, y se avisa
            markup += f"<p style='color:#999; font-size:{BODY_PX}px'>↕ Rueda del mouse para ver más</p>"
            self._measure(markup)
            height = limit
        self._view.setFixedSize(self.WIDTH, height)
        self.setFixedSize(self.WIDTH + 2, height + 2)       # +2: borde de 1 px
        self._view.verticalScrollBar().setValue(0)
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
