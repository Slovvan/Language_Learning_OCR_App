"""Worker que corre OCR + traducción en un hilo aparte para no congelar la interfaz."""
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

import Translator.japanese as japanese
import Translator.languages as languages
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
        self.language = "en"                  # idioma del juego (clave de languages.PACKS)
        self.ocr_mode = japanese.Mode.NORMAL  # modo de OCR del japonés (normal / solo kana)

    def _load_translator(self):
        pack = languages.get_pack(self.language)
        self._translator = None
        self._translator_error = ""
        try:
            self.status.emit(f"Cargando traductor {pack.source_name} → {pack.target_name} "
                             "(la primera vez descarga el modelo)...")
            translator = Translator(pack=pack)
            translator.load()
            self._translator = translator
        except Exception as exc:  # noqa: BLE001
            self._translator_error = str(exc)

    @pyqtSlot(str, str)
    def configure(self, language, ocr_mode):
        """Cambia el idioma del juego y el modo de OCR. Solo se recarga el traductor si cambia
        el idioma (y se descarta el anterior para no gastar memoria)."""
        try:
            self.ocr_mode = japanese.Mode(ocr_mode)
        except ValueError:
            self.ocr_mode = japanese.Mode.NORMAL
        if language != self.language or (self._translator is None and not self._translator_error):
            self.language = language
            if self._engine is not None:
                self._load_translator()
        if self._engine is not None:
            self.ready.emit()
            if self._translator is None:
                self.failed.emit(f"Traductor no disponible: {self._translator_error}")

    @pyqtSlot()
    def load(self):
        """Se ejecuta al iniciar el hilo: carga OCR y traductor una sola vez."""
        try:
            self.status.emit("Cargando OCR...")
            self._engine = OcrEngine()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"No se pudo cargar el OCR: {exc}")
            return

        self._load_translator()

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
            if languages.get_pack(self.language).has_ocr_modes:   # japonés: filas, furigana, confusiones
                res = japanese.process(res, self.ocr_mode)
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