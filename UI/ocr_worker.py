"""Worker que corre OCR + traducción en un hilo aparte para no congelar la interfaz."""
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from OCR.ocr_engine import OcrEngine
from Translator.translator import Translator


class OcrWorker(QObject):
    ready = pyqtSignal()
    status = pyqtSignal(str)
    result = pyqtSignal(object)     # OcrResult (texto original)
    translated = pyqtSignal(str)    # texto traducido
    card_translated = pyqtSignal(int, str, str)   # id de tarjeta, frase traducida, palabra traducida
    done = pyqtSignal()             # terminó una captura (con o sin error)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._engine = None
        self._translator = None
        self._translator_error = ""

    @pyqtSlot()
    def load(self):
        """Se ejecuta al iniciar el hilo: carga OCR y traductor una sola vez."""
        try:
            self.status.emit("Cargando OCR...")
            self._engine = OcrEngine()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"No se pudo cargar el OCR: {exc}")
            return

        try:
            self.status.emit("Cargando traductor (la primera vez descarga ~150 MB)...")
            translator = Translator()
            translator.load()
            self._translator = translator
        except Exception as exc:  # noqa: BLE001
            self._translator_error = str(exc)

        # El OCR funciona aunque el traductor falle; avisamos después de "ready".
        self.ready.emit()
        if self._translator is None:
            self.failed.emit(f"Traductor no disponible: {self._translator_error}")

    @pyqtSlot(object, bool)
    def process(self, image, merge_lines):
        if self._engine is None:
            self.failed.emit("El OCR todavía no está listo.")
            self.done.emit()
            return
        try:
            res = self._engine.read(image)
            self.result.emit(res)
            if res.text.strip():
                if self._translator is None:
                    self.failed.emit(f"Traductor no disponible: {self._translator_error}")
                else:
                    self.translated.emit(self._translator.translate(res.text, merge_lines))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Error: {exc}")
        finally:
            self.done.emit()

    @pyqtSlot(int, str, str)
    def translate_for_card(self, card_id, sentence, word):
        """Traduce la frase de una tarjeta y, si se pide, la palabra suelta.

        La palabra solo se pide cuando no tiene entrada en el diccionario (p. ej. «mana»).
        Si la frase ya se tradujo en la captura actual, sale de la caché del traductor.
        """
        if self._translator is None:
            self.card_translated.emit(card_id, "", "")
            return
        try:
            sentence_es = self._translator.translate_sentence(sentence) if sentence else ""
            word_es = self._translator.translate_sentence(word) if word else ""
            self.card_translated.emit(card_id, sentence_es, word_es)
        except Exception as exc:  # noqa: BLE001
            self.status.emit(f"No se pudo traducir la tarjeta: {exc}")