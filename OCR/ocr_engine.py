"""Envoltorio de RapidOCR con preprocesado básico."""
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class OcrResult:
    text: str
    lines: list = field(default_factory=list)  # [(texto, confianza), ...]
    # Una caja (x0, y0, x1, y1) por línea, en píxeles de la imagen que se le dio a read()
    # (sin la ampliación interna). Sirve para poner el texto traducido encima del original.
    boxes: list = field(default_factory=list)


def preprocess(img: np.ndarray, scale: float = 2.0, binarize: bool = False) -> np.ndarray:
    """Mejora la imagen antes del OCR.

    - Escalar x2 ayuda mucho con fuentes pequeñas de juegos.
    - Binarizar solo conviene con texto de alto contraste; en fondos
      complejos puede empeorar, por eso está desactivado por defecto.
    """
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if binarize:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        img = cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)
    return img


def _bbox(points, scale: float) -> tuple:
    """Caja envolvente (x0, y0, x1, y1) de los 4 puntos de una línea, en píxeles originales."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2) / scale
    return (float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max()))


class OcrEngine:
    """Carga el modelo una sola vez; reutilízalo para todas las capturas."""

    def __init__(self):
        try:  # paquete nuevo: `pip install rapidocr`
            from rapidocr import RapidOCR
            self._new_api = True
        except ImportError:  # paquete antiguo: `pip install rapidocr-onnxruntime`
            from rapidocr_onnxruntime import RapidOCR
            self._new_api = False
        self._engine = RapidOCR()

    def read(self, img: np.ndarray, scale: float = 2.0, binarize: bool = False) -> OcrResult:
        img = preprocess(img, scale, binarize)
        lines, boxes = [], []

        if self._new_api:
            out = self._engine(img)
            txts = out.txts or ()
            scores = out.scores or ()
            found = out.boxes if out.boxes is not None else ()
            lines = list(zip(txts, scores))
            boxes = [_bbox(b, scale) for b in found][:len(lines)]
        else:
            result, _ = self._engine(img)
            for box, txt, score in (result or []):
                lines.append((txt, float(score)))
                boxes.append(_bbox(box, scale))

        text = "\n".join(t for t, _ in lines)
        return OcrResult(text=text, lines=lines, boxes=boxes)
