"""Cuadro de texto que detecta la palabra bajo el mouse (hover y clic) y ventanita con el diccionario."""
import html
import re

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QTextCursor, QTextDocument
from PyQt5.QtWidgets import QApplication, QFrame, QTextBrowser, QTextEdit, QVBoxLayout

import Translator.languages as languages
from DB.dictionary import POS_LABEL

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


def render_html(result, saved: bool = False) -> str:
    """HTML sencillo (subconjunto que entiende QTextDocument) para un LookupResult."""
    esc = html.escape
    parts = [SAVED_BADGE] if saved else []
    previous = None
    for entry in result.entries:
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
        parts.append(f"<p style='margin:6px 0 3px 0'>{head}</p>")
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
            self._timer.start()

    def _emit_pending(self):
        if self._pending:
            self._shown = True
            self.word_hovered.emit(*self._pending)

    def _end(self):
        self._timer.stop()
        self._key = None
        self._pending = None
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
        self._view = QTextBrowser()
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
