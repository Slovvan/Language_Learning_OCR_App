"""Lo que se hace con una palabra (ver significado, guardarla, marcarla) en un solo lugar.

Lo comparten la capa sobre la pantalla congelada, la ventana principal y el registro de frases,
así las tres se comportan igual.
"""
from PyQt5.QtCore import QObject, pyqtSignal

from Cards.cards import build_card
from Cards.sentences import sentence_at
from UI.word_popup import iter_words


class WordService(QObject):
    saved_changed = pyqtSignal()                  # se guardó o borró una tarjeta: refrescar marcas
    card_requested = pyqtSignal(int, str, str)    # id de tarjeta, frase, palabra a traducir ("" = ninguna)
    message = pyqtSignal(str)                     # aviso corto para mostrar al usuario

    def __init__(self, dictionary, store, popup, parent=None):
        super().__init__(parent)
        self.dictionary = dictionary
        self.store = store
        self.popup = popup

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
        sentence = sentence_at(text, start, merge) or ""
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
        if not self.dictionary.available:
            self.popup.show_message("Diccionario no encontrado: ejecuta build_dictionary.py", global_pos)
            return
        saved = self.is_saved(word, compound)
        result = self.dictionary.lookup(word, compound)
        if result is None:
            self.popup.show_message(f"«{word}»: sin entrada en el diccionario", global_pos, saved)
        else:
            self.popup.show_result(result, global_pos, saved)

    def hide_popup(self):
        self.popup.hide()
