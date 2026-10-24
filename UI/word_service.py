"""Lo que se hace con una palabra (ver significado, guardarla, marcarla) en un solo lugar.

Lo comparten la capa sobre la pantalla congelada, la ventana principal y el registro de frases,
así las tres se comportan igual.
"""
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from Cards.cards import build_card
import Translator.languages as languages
from UI.word_popup import iter_words


HIDE_AFTER_WORD_MS = 650     # espera antes de ocultar la ventanita al dejar la palabra
HIDE_AFTER_LEAVE_MS = 250    # ...y al salir el mouse de la propia ventanita


class WordService(QObject):
    saved_changed = pyqtSignal()                  # se guardó o borró una tarjeta: refrescar marcas
    card_requested = pyqtSignal(int, str, str)    # id de tarjeta, frase, palabra a traducir ("" = ninguna)
    message = pyqtSignal(str)                     # aviso corto para mostrar al usuario

    def __init__(self, dictionary, store, popup, parent=None):
        super().__init__(parent)
        self.dictionary = dictionary
        self.store = store
        self.popup = popup
        # El mouse puede ir de la palabra a la ventanita para desplazarla: al dejar la palabra,
        # la ventanita espera un momento antes de ocultarse (y se queda si el mouse entra en ella).
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_if_idle)
        if hasattr(popup, "entered"):
            popup.entered.connect(self._hide_timer.stop)
            popup.left.connect(lambda: self._hide_timer.start(HIDE_AFTER_LEAVE_MS))

    # --- palabras guardadas ---
    def is_saved(self, word: str, compound: str = "") -> bool:
        return self.dictionary.key_for(word, compound) in self.store.keys

    def marks(self, text: str) -> list:
        """Rangos (inicio, fin) de `text` que son palabras ya guardadas."""
        if not len(self.store):
            return []
        return [(start, end) for start, end, word, compound in iter_words(text)
                if self.is_saved(word, compound)]

    def click(self, word: str, compound: str, text: str, start: int, merge: bool):
        """Guarda la palabra con su significado y su frase (una vez por lema).

        `text` es el texto completo donde está la palabra y `start` su posición, para sacar
        la frase. Devuelve la tarjeta nueva o None si ya estaba guardada.
        """
        key = self.dictionary.key_for(word, compound)
        if self.store.has(key):
            self.message.emit(f"«{key}» ya estaba guardada.")
            return None
        sentence = languages.active().sentence_at(text, start, merge) or ""
        result = self.dictionary.lookup(word, compound)
        card = self.store.add(build_card(word, key, result, sentence))
        # Sin entrada en el diccionario (p. ej. «mana»): se pide también la traducción de la palabra.
        self.card_requested.emit(card.id, sentence, "" if result is not None else word)
        self.message.emit(f"✓ «{card.word}» guardada ({len(self.store)} en la lista).")
        self.saved_changed.emit()
        return card

    def card_translated(self, card_id: int, sentence_es: str, word_es: str) -> bool:
        """Llega la traducción de la frase (y, si hacía falta, de la palabra). ¿Cambió la tarjeta?"""
        card = self.store.get(card_id)
        if card is None:                          # se borró mientras se traducía
            return False
        changes = {}
        if sentence_es and not card.sentence_es:
            changes["sentence_es"] = sentence_es
        if word_es and not card.meaning:
            changes["meaning"] = f"{word_es} (traducción automática)"
        if changes:
            self.store.update(card_id, **changes)
        return bool(changes)

    # --- ventanita del diccionario ---
    def hover(self, word: str, compound: str, global_pos):
        self._hide_timer.stop()
        if not self.dictionary.available:
            script = getattr(self.dictionary, "build_hint", "build_dictionary.py")
            self.popup.show_message(f"Diccionario no encontrado: ejecuta {script}", global_pos)
            return
        saved = self.is_saved(word, compound)
        result = self.dictionary.lookup(word, compound, full=True)
        if result is None:
            self.popup.show_message(f"«{word}»: sin entrada en el diccionario", global_pos, saved)
        else:
            self.popup.show_result(result, global_pos, saved)

    def hide_popup(self):
        """Oculta la ventanita ya."""
        self._hide_timer.stop()
        self.popup.hide()

    def hover_left(self):
        """El mouse dejó la palabra: oculta la ventanita tras una breve espera, salvo que entre en ella."""
        self._hide_timer.start(HIDE_AFTER_WORD_MS)

    def _hide_if_idle(self):
        if not self.popup.underMouse():
            self.popup.hide()

    def scroll_popup(self, delta: int):
        """Rueda del mouse sobre el texto mientras la ventanita está visible."""
        if self.popup.isVisible() and hasattr(self.popup, "scroll_by"):
            self.popup.scroll_by(delta)
            self._hide_timer.stop()
