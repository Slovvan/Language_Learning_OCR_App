"""Fase 3: cuadro de texto que detecta la palabra bajo el mouse y ventanita con el diccionario."""
import html
import re

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QTextDocument
from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QTextEdit, QVBoxLayout

_L = r"[^\W\d_]"   # una letra (Unicode)
_WORD_RE = re.compile(rf"{_L}+(?:['’]{_L}+)*")           # palabra, con apóstrofos: don't
_COMPOUND_RE = re.compile(rf"{_L}+(?:['’-]{_L}+)*")      # compuesto con guiones: well-known

POS_LABEL = {"n": "sust.", "v": "verbo", "a": "adj.", "r": "adv."}
MAX_DEFINITION = 120   # caracteres; las definiciones más largas se cortan con "…"


def find_word(text: str, index: int):
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


def render_html(result) -> str:
    """HTML sencillo (subconjunto que entiende QLabel) para un LookupResult."""
    esc = html.escape
    parts = []
    previous = None
    for entry in result.entries:
        if previous is not None and previous.kind == "function" and previous.lemma == entry.lemma:
            head = f"<span style='color:#888'>{esc(entry.lemma)} — otros significados</span>"
        else:
            head = f"<span style='font-size:16px; font-weight:600'>{esc(entry.lemma)}</span>"
            if entry.lemma.lower() != result.query.lower():
                head += f" <span style='color:#888'>← {esc(result.query)}</span>"
        previous = entry
        parts.append(f"<p style='margin:0 0 3px 0'>{head}</p>")
        if entry.note:
            parts.append(f"<p style='margin:0 0 3px 0; color:#888'><i>{esc(entry.note)}</i></p>")
        for n, s in enumerate(entry.senses, 1):
            definition = s.definition if len(s.definition) <= MAX_DEFINITION else \
                s.definition[:MAX_DEFINITION].rsplit(" ", 1)[0] + "…"
            lines = [f"<b>{n}.</b> <i style='color:#8a4b1f'>{POS_LABEL.get(s.pos, s.pos)}</i> "
                     f"{esc(definition)}"]
            if s.spanish:
                lines.append(f"<span style='color:#1b6b2f'>ES: {esc(', '.join(s.spanish[:4]))}</span>")
            if entry.kind == "function" and s.example:
                lines.append(f"<span style='color:#555'><i>{esc(s.example)}</i></span>")
            if s.synonyms:
                lines.append(f"<span style='color:#444'>≈ {esc(', '.join(s.synonyms[:5]))}</span>")
            if s.antonyms:
                lines.append(f"<span style='color:#a02020'>≠ {esc(', '.join(s.antonyms[:4]))}</span>")
            parts.append("<p style='margin:0 0 5px 0'>" + "<br>".join(lines) + "</p>")
        extra = entry.total - len(entry.senses)
        if extra > 0:
            parts.append(f"<p style='margin:0 0 6px 0; color:#888'>+{extra} significado(s) más</p>")
    return "".join(parts)


class HoverTextEdit(QTextEdit):
    """QTextEdit que avisa cuando el mouse se queda quieto sobre una palabra.

    word_hovered(palabra, compuesto, posición_global): tras `delay_ms` sobre la misma palabra.
    hover_ended(): hay que ocultar la ventanita (el mouse cambió de palabra o salió del texto,
    hubo clic, desplazamiento o cambió el contenido).
    """

    word_hovered = pyqtSignal(str, str, QPoint)
    hover_ended = pyqtSignal()

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

    def viewportEvent(self, e):
        if e.type() == QEvent.Leave:
            self._end()
        return super().viewportEvent(e)

    def mousePressEvent(self, e):
        self._end()
        super().mousePressEvent(e)


class WordPopup(QFrame):
    """Ventanita flotante (no roba el foco ni intercepta el mouse) con el resultado."""

    WIDTH = 380

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setObjectName("wordPopup")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        # Colores fijos: así se lee igual con el tema claro u oscuro del sistema.
        self.setStyleSheet(
            "QFrame#wordPopup { background:#fffdf6; border:1px solid #b9b192; border-radius:6px; }"
            "QLabel { color:#222; background:transparent; }")
        self._label = QLabel()
        self._label.setTextFormat(Qt.RichText)
        self._label.setWordWrap(True)
        self._label.setFixedWidth(self.WIDTH)
        self._label.setMargin(9)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

    def show_result(self, result, global_pos: QPoint):
        self._show(render_html(result), global_pos)

    def show_message(self, text: str, global_pos: QPoint):
        self._show(f"<span style='color:#666'>{html.escape(text)}</span>", global_pos)

    def _show(self, markup: str, global_pos: QPoint):
        self._label.setText(markup)
        # QLabel con saltos de línea automáticos calcula mal su altura (sizeHint ignora el
        # ancho fijo y heightForWidth arrastra valores anteriores), así que la altura se
        # mide aparte con un QTextDocument y se fija a mano. Si no, el texto sale cortado.
        doc = QTextDocument()
        doc.setDefaultFont(self._label.font())
        doc.setHtml(markup)
        doc.setTextWidth(self.WIDTH - 2 * self._label.margin())
        height = int(doc.size().height()) + 2 * self._label.margin() + 4
        self._label.setFixedSize(self.WIDTH, height)
        self.setFixedSize(self.WIDTH + 2, height + 2)   # +2: borde de 1 px
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x, y = global_pos.x() + 14, global_pos.y() + 22
        if x + self.width() > area.right():
            x = max(area.left(), global_pos.x() - self.width() - 14)
        if y + self.height() > area.bottom():
            y = max(area.top(), global_pos.y() - self.height() - 14)
        self.move(x, y)
        self.show()