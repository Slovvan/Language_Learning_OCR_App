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
    # Solo japonés (modo furigana): [(inicio, fin, lectura)] sobre `text`. Lo rellena japanese.process.
    readings: list = field(default_factory=list)
    # Solo japonés, modo vertical: las líneas son columnas (los caracteres se apilan de arriba abajo).
    vertical: bool = False


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


SQUARE_MARGIN = 20        # margen (px) arriba y abajo al rellenar una columna de texto vertical


def pad_for_vertical(img: np.ndarray):
    """(imagen con relleno, (dx, dy)): la imagen centrada en un lienzo del color de su borde, al menos tan ancho como alto.

    Una región de texto vertical es una tira estrecha y alta; el detector la reescala según su lado corto y los
    kana pequeños (っ, ゃ) se leen como «9», «2», «つ», «て»… (con la misma región, 2 aciertos de 20; rellenada hasta
    ser cuadrada, 19 de 20 y 5 veces más rápido). El relleno es del color del borde de la región, así no añade tinta.
    (dx, dy) es lo que hay que restar a las cajas para volver a las coordenadas de la imagen original."""
    h, w = img.shape[:2]
    if h < 2 or w < 2:
        return img, (0, 0)
    extra = max(0, h - w)
    left = max(10, extra // 2)
    right = max(10, extra - extra // 2)
    ring = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    fill = [int(v) for v in np.median(ring, axis=0)]
    padded = cv2.copyMakeBorder(img, SQUARE_MARGIN, SQUARE_MARGIN, left, right, cv2.BORDER_CONSTANT, value=fill)
    return padded, (left, SQUARE_MARGIN)


def _bbox(points, scale: float) -> tuple:
    """Caja envolvente (x0, y0, x1, y1) de los 4 puntos de una línea, en píxeles originales."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2) / scale
    return (float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max()))


class OcrEngine:
    """Carga el modelo una sola vez; reutilízalo para todas las capturas."""

    def __init__(self, lang: str = ""):
        """`lang` = "latin" usa el reconocedor de alfabeto latino (francés: é è ç à ù ô œ « »)."""
        self.lang = lang
        try:  # paquete nuevo: `pip install rapidocr`
            from rapidocr import RapidOCR
            self._new_api = True
        except ImportError:  # paquete antiguo: `pip install rapidocr-onnxruntime`
            from rapidocr_onnxruntime import RapidOCR
            self._new_api = False
        self._engine = None
        if lang == "latin" and self._new_api:
            try:
                from rapidocr import LangRec, OCRVersion
                # El reconocedor latino solo existe en versión «mobile» (con «small» RapidOCR lo rechaza).
                self._engine = RapidOCR(params={"Rec.lang_type": LangRec.LATIN,
                                                "Rec.ocr_version": OCRVersion.PPOCRV5,
                                                "Rec.model_type": "mobile"})
            except Exception:  # noqa: BLE001  (versión sin modelo latino: se usa el de serie)
                self._engine = None
        if self._engine is None:
            self._engine = RapidOCR()

    # Variantes de preparación y alturas (px) a las que se amplía el texto antes de reconocerlo.
    STRIP_VARIANTS = ("color",)               # probadas: "stretch" y "otsu" empeoran (lecturas seguras pero equivocadas)
    STRIP_HEIGHTS = (40,)

    @staticmethod
    def _ink_crop(crop: np.ndarray):
        """(recorte ajustado al texto, color de fondo) o (None, None) si no hay tinta."""
        h, w = crop.shape[:2]
        ring = np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]]).astype(np.float32)
        bg = np.median(ring, axis=0)
        dist = np.abs(crop.astype(np.float32) - bg).max(axis=2)
        mask = dist > max(30.0, 0.35 * float(np.percentile(dist, 99)))
        if mask.sum() < 6:
            return None, None
        ys, xs = np.where(mask)
        y0, y1 = max(0, ys.min() - 2), min(h, ys.max() + 3)
        x0, x1 = max(0, xs.min() - 2), min(w, xs.max() + 3)
        return crop[y0:y1, x0:x1], bg

    @staticmethod
    def _variant(tight: np.ndarray, bg, name: str):
        if name == "stretch":                                   # estira el contraste: texto fino y de poco contraste
            lo = np.percentile(tight, 2, axis=(0, 1))
            hi = np.percentile(tight, 98, axis=(0, 1))
            out = (tight.astype(np.float32) - lo) / np.maximum(hi - lo, 1.0) * 255.0
            return np.clip(out, 0, 255).astype(np.uint8), (np.clip((np.asarray(bg) - lo) / np.maximum(hi - lo, 1.0) * 255.0, 0, 255))
        if name == "otsu":                                      # binarizado: quita contornos y degradados
            gray = cv2.cvtColor(tight, cv2.COLOR_BGR2GRAY)
            _t, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            if bw.mean() < 127:                                 # texto claro sobre oscuro -> texto negro sobre blanco
                bw = 255 - bw
            return cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR), np.array([255.0, 255.0, 255.0])
        return tight, np.asarray(bg, dtype=np.float32)

    def read_strip(self, img: np.ndarray, window):
        """Lee solo-texto (sin detectar) una franja de `img`: `window` = (x0, y0, x1, y1).

        Se recorta justo al texto y se amplía a una altura fija (el reconocedor trabaja a 48 px;
        un furigana de 10 px recortado con mucho fondo se lee mal). Se prueban varias variantes de
        preparación y se devuelve (texto, confianza) de la más segura, o None si no hay nada.
        Usa un motor propio en modo solo-reconocimiento: RapidOCR se queda en ese modo tras una
        llamada con use_det=False y dejaría de detectar.
        """
        if not self._new_api:
            return None
        if getattr(self, "_rec_engine", None) is None:
            from rapidocr import LangRec, OCRVersion, RapidOCR
            self._rec_engine = RapidOCR(params={"Rec.lang_type": LangRec.JAPAN,
                                                "Rec.ocr_version": OCRVersion.PPOCRV6})
        x0, y0, x1, y1 = (int(round(v)) for v in window)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(img.shape[1], x1), min(img.shape[0], y1)
        if x1 - x0 < 6 or y1 - y0 < 6:
            return None
        crop = img[y0:y1, x0:x1]
        if crop.std() < 8:                                      # sin tinta: nada que leer
            return None
        tight, bg = self._ink_crop(crop)
        if tight is None:
            return None
        best = None
        for name in self.STRIP_VARIANTS:
            prepared, fill = self._variant(tight, bg, name)
            for height in self.STRIP_HEIGHTS:
                k = float(np.clip(height / prepared.shape[0], 1.5, 10.0))
                big = cv2.resize(prepared, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
                big = cv2.copyMakeBorder(big, 10, 10, 10, 10, cv2.BORDER_CONSTANT,
                                         value=[float(v) for v in fill])
                out = self._rec_engine(big, use_det=False, use_cls=False, use_rec=True)
                if out.txts and (best is None or float(out.scores[0]) > best[1]):
                    best = (out.txts[0], float(out.scores[0]))
        return best

    def read(self, img: np.ndarray, scale: float = 2.0, binarize: bool = False, vertical: bool = False) -> OcrResult:
        """`vertical`: texto en columnas; la imagen se rellena hasta ser cuadrada (ver pad_for_vertical)."""
        shift = (0, 0)
        if vertical:
            img, shift = pad_for_vertical(img)
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

        if shift != (0, 0):
            boxes = [(b[0] - shift[0], b[1] - shift[1], b[2] - shift[0], b[3] - shift[1]) for b in boxes]
        text = "\n".join(t for t, _ in lines)
        return OcrResult(text=text, lines=lines, boxes=boxes)


# ---------------------------------------------------------------------------- filas (idiomas con espacios)
ROW_OVERLAP = 0.5      # solapamiento vertical mínimo (fracción de la caja más baja) para ser de la misma fila
ROW_GAP = 2.5          # hueco máximo entre dos cajas de una fila, en alturas de línea; más allá son bloques distintos


def merge_rows(res: OcrResult) -> OcrResult:
    """Une en una sola línea las cajas que están en la misma fila (de izquierda a derecha).

    Con letras muy separadas (fuentes de juego con espaciado ancho) el detector parte una frase en palabras sueltas
    y cada una sale como línea aparte. Aquí se vuelven a juntar: mismas filas, huecos normales entre palabras.
    Para idiomas con espacios (inglés, francés); el japonés tiene su propio procesado (japanese.process).
    """
    if len(res.boxes) != len(res.lines) or len(res.lines) < 2:
        return res
    items = [(t, s, tuple(b)) for (t, s), b in zip(res.lines, res.boxes) if t.strip()]
    items.sort(key=lambda it: (it[2][1] + it[2][3]) / 2)
    rows = []                                           # cada fila: [(texto, confianza, caja)]
    for it in items:
        box = it[2]
        for row in rows:
            top, bottom = min(b[1] for _t, _s, b in row), max(b[3] for _t, _s, b in row)
            overlap = min(bottom, box[3]) - max(top, box[1])
            if overlap >= ROW_OVERLAP * min(bottom - top, box[3] - box[1]):
                row.append(it)
                break
        else:
            rows.append([it])
    merged = []                                         # (texto, confianza, caja)
    for row in rows:
        row.sort(key=lambda it: it[2][0])
        chunk = [row[0]]
        for it in row[1:]:
            height = max(b[3] - b[1] for _t, _s, b in chunk + [it])
            if it[2][0] - max(b[2] for _t, _s, b in chunk) <= ROW_GAP * height:
                chunk.append(it)
            else:
                merged.append(chunk)
                chunk = [it]
        merged.append(chunk)
    out_lines, out_boxes = [], []
    for chunk in sorted(merged, key=lambda c: ((min(b[1] for _t, _s, b in c) + max(b[3] for _t, _s, b in c)) / 2,
                                                min(b[0] for _t, _s, b in c))):
        text = " ".join(t.strip() for t, _s, _b in chunk)
        weight = sum(len(t) for t, _s, _b in chunk) or 1
        score = sum(float(s) * len(t) for t, s, _b in chunk) / weight
        out_lines.append((text, score))
        out_boxes.append((min(b[0] for _t, _s, b in chunk), min(b[1] for _t, _s, b in chunk),
                          max(b[2] for _t, _s, b in chunk), max(b[3] for _t, _s, b in chunk)))
    if len(out_lines) == len(res.lines):                # nada que unir: se devuelve tal cual
        return res
    from dataclasses import replace
    return replace(res, lines=out_lines, boxes=out_boxes, text="\n".join(t for t, _ in out_lines))
