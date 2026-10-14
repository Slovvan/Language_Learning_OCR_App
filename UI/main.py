import onnxruntime  # noqa: F401
import ctranslate2  # noqa: F401

import sys

from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel,
                             QPushButton, QTextEdit, QVBoxLayout, QWidget)

from UI.capture import RegionSelector, bgr_to_qpixmap, grab_screen
from UI.ocr_worker import OcrWorker

HOTKEY = "<ctrl>+<alt>+o"


class HotkeyBridge(QObject):
    """Convierte el atajo global (hilo de pynput) en una señal de Qt."""
    triggered = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._listener = None
        try:
            from pynput import keyboard
            self._listener = keyboard.GlobalHotKeys({HOTKEY: self.triggered.emit})
            self._listener.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[aviso] Atajo global no disponible: {exc}")

    def stop(self):
        if self._listener:
            self._listener.stop()


class MainWindow(QWidget):
    process_requested = pyqtSignal(object, bool)   # imagen, unir_lineas

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR para juegos - Fase 2 (EN → ES)")
        self.resize(560, 640)
        self.selector = None
        self._ocr_info = ""

        # --- UI ---
        self.btn_capture = QPushButton(f"Capturar región ({HOTKEY})")
        self.btn_capture.setEnabled(False)
        self.btn_copy_src = QPushButton("Copiar original")
        self.btn_copy_dst = QPushButton("Copiar traducción")
        self.chk_merge = QCheckBox("Unir líneas partidas (diálogos)")
        self.chk_merge.setChecked(True)
        self.chk_merge.setToolTip(
            "Actívalo para cuadros de diálogo donde una frase ocupa varias líneas.\n"
            "Desactívalo para menús o listas donde cada línea es independiente."
        )
        self.status = QLabel("Iniciando...")
        self.status.setWordWrap(True)
        self.preview = QLabel("(aquí aparece la región capturada)")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(110)
        self.preview.setStyleSheet("border: 1px dashed gray;")
        self.output = QTextEdit()
        self.output.setPlaceholderText("Texto original (OCR)...")
        self.translation = QTextEdit()
        self.translation.setPlaceholderText("Traducción al español...")

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_capture)
        buttons.addWidget(self.btn_copy_src)
        buttons.addWidget(self.btn_copy_dst)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.chk_merge)
        layout.addWidget(self.status)
        layout.addWidget(self.preview)
        layout.addWidget(QLabel("Original (inglés)"))
        layout.addWidget(self.output, 1)
        layout.addWidget(QLabel("Traducción (español)"))
        layout.addWidget(self.translation, 1)

        # --- hilo del OCR + traductor (los modelos se cargan una vez, dentro del hilo) ---
        self.thread = QThread()
        self.worker = OcrWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.load)
        self.process_requested.connect(self.worker.process)
        self.worker.status.connect(self.status.setText)
        self.worker.ready.connect(self.on_ready)
        self.worker.result.connect(self.on_result)
        self.worker.translated.connect(self.on_translated)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.thread.start()

        # --- señales de la UI ---
        self.btn_capture.clicked.connect(self.start_capture)
        self.btn_copy_src.clicked.connect(lambda: self.copy(self.output))
        self.btn_copy_dst.clicked.connect(lambda: self.copy(self.translation))

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
        self.translation.clear()
        self.status.setText("Procesando...")
        self.btn_capture.setEnabled(False)
        self.process_requested.emit(image, self.chk_merge.isChecked())

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
            self._ocr_info = f"{len(res.lines)} línea(s), confianza media {avg:.0%}"
            self.status.setText(f"OCR: {self._ocr_info}. Traduciendo...")
        else:
            self._ocr_info = ""
            self.status.setText("Sin texto detectado.")

    def on_translated(self, text):
        self.translation.setPlainText(text)
        self.status.setText(f"Listo. {self._ocr_info}")

    def on_done(self):
        self.btn_capture.setEnabled(True)

    def on_failed(self, msg):
        self.status.setText(msg)
        self.btn_capture.setEnabled(self.worker._engine is not None)

    def copy(self, box):
        QGuiApplication.clipboard().setText(box.toPlainText())

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