"""Worker que corre el OCR en un hilo aparte para no congelar la interfaz."""
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from OCR.ocr_engine import OcrEngine, OcrResult


class OcrWorker(QObject):
    ready = pyqtSignal()
    result = pyqtSignal(object)   # OcrResult
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._engine = None

    @pyqtSlot()
    def load(self):
        """Se ejecuta al iniciar el hilo: carga el modelo una sola vez."""
        try:
            self._engine = OcrEngine()
            self.ready.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"No se pudo cargar el OCR: {exc}")

    @pyqtSlot(object)
    def process(self, image):
        if self._engine is None:
            self.failed.emit("El OCR todavía no está listo.")
            return
        try:
            self.result.emit(self._engine.read(image))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Error en el OCR: {exc}")