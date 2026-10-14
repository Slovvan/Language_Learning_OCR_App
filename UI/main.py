import sys
import onnxruntime
from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                             QTextEdit, QVBoxLayout, QWidget)

from UI.capture import RegionSelector, bgr_to_qpixmap, grab_screen
from UI.ocr_worker import OcrWorker
from pynput import keyboard

HOTKEY = "<ctrl>+<alt>+o"


class HotkeyBridge(QObject):
    """Convierte el atajo global (hilo de pynput) en una señal de Qt."""
    triggered = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._listener = None
        try:
            
            self._listener = keyboard.GlobalHotKeys({HOTKEY: self.triggered.emit})
            self._listener.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[aviso] Atajo global no disponible: {exc}")

    def stop(self):
        if self._listener:
            self._listener.stop()


class MainWindow(QWidget):
    process_requested = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR para juegos - Etapa 1")
        self.resize(520, 480)
        self.selector = None

        # --- UI ---
        self.btn_capture = QPushButton(f"Capturar región ({HOTKEY})")
        self.btn_capture.setEnabled(False)
        self.btn_copy = QPushButton("Copiar texto")
        self.status = QLabel("Cargando modelo OCR...")
        self.preview = QLabel("(aquí aparece la región capturada)")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(120)
        self.preview.setStyleSheet("border: 1px dashed gray;")
        self.output = QTextEdit()
        self.output.setPlaceholderText("Texto reconocido...")

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_capture)
        buttons.addWidget(self.btn_copy)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(self.preview)
        layout.addWidget(self.output, 1)

        # --- hilo del OCR (el modelo se carga una vez, dentro del hilo) ---
        self.thread = QThread()
        self.worker = OcrWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.load)
        self.process_requested.connect(self.worker.process)
        self.worker.ready.connect(self.on_ready)
        self.worker.result.connect(self.on_result)
        self.worker.failed.connect(self.on_failed)
        self.thread.start()

        # --- señales de la UI ---
        self.btn_capture.clicked.connect(self.start_capture)
        self.btn_copy.clicked.connect(self.copy_text)

        # --- atajo global (opcional) ---
        self.hotkey = HotkeyBridge()
        self.hotkey.triggered.connect(self.start_capture)

    # --- flujo de captura ---
    def start_capture(self):
        if not self.btn_capture.isEnabled() or self.selector is not None:
            return
        self.hide()  # que la propia ventana no salga en la captura
        QTimer.singleShot(200, self._grab_and_select)

    def _grab_and_select(self):
        frame = grab_screen()
        self.selector = RegionSelector(frame)
        self.selector.selected.connect(self.on_region)
        self.selector.cancelled.connect(self.on_cancel)
        self.selector.start()

    def _close_selector(self):
        if self.selector is not None:
            self.selector.deleteLater()
            self.selector = None

    def on_region(self, image):
        self._close_selector()
        self.show()
        self.activateWindow()
        pix = bgr_to_qpixmap(image)
        self.preview.setPixmap(pix.scaledToWidth(min(pix.width(), 480), Qt.SmoothTransformation))
        self.status.setText("Procesando...")
        self.btn_capture.setEnabled(False)
        self.process_requested.emit(image)

    def on_cancel(self):
        self._close_selector()
        self.show()
        self.status.setText("Captura cancelada.")

    # --- resultados del worker ---
    def on_ready(self):
        self.status.setText("Listo. Pulsa el botón o usa el atajo.")
        self.btn_capture.setEnabled(True)

    def on_result(self, res):
        self.output.setPlainText(res.text or "(no se detectó texto)")
        if res.lines:
            avg = sum(s for _, s in res.lines) / len(res.lines)
            self.status.setText(f"Listo. {len(res.lines)} línea(s), confianza media {avg:.0%}")
        else:
            self.status.setText("Listo. Sin texto detectado.")
        self.btn_capture.setEnabled(True)

    def on_failed(self, msg):
        self.status.setText(msg)
        self.btn_capture.setEnabled(self.worker._engine is not None)

    def copy_text(self):
        QGuiApplication.clipboard().setText(self.output.toPlainText())

    def closeEvent(self, event):
        self.hotkey.stop()
        self.thread.quit()
        self.thread.wait(3000)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())