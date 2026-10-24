"""Prueba del OCR con texto en japonés (solo OCR: sin Qt, sin traducción).

Uso:
    python test_ocr_jp.py captura1.png captura2.png     # una o varias imágenes
    python test_ocr_jp.py carpeta_con_capturas          # todas las imágenes de una carpeta
    python test_ocr_jp.py --demo                        # genera una imagen de ejemplo y la lee
    python test_ocr_jp.py --capture                     # recortes de pantalla: eliges la región y se lee

Opciones:
    --scales 1,2,3    ampliaciones a probar (por defecto 1,2; ayudan con letra pequeña)
    --capture         modo recortes: Enter -> cuenta atrás -> la pantalla se congela -> arrastra para
                      elegir la región -> se lee y se guarda el recorte (captura_001.png...).
                      Cuenta atrás para que cambies al juego. Hay que ejecutarlo desde la carpeta
                      del proyecto (usa capture.py y overlay.py). Esc cancela una selección.
    --modes LISTA     modos de japanese.py a mostrar tras el texto crudo: normal, furigana, kana
                      (por defecto normal,furigana). Ve el resultado de cada uno con la misma imagen.
    --delay N         segundos de cuenta atrás en --capture (por defecto 3)
    --compare         además prueba el modelo japonés antiguo (PP-OCRv4) para comparar
    --out CARPETA     dónde guardar resultados (por defecto ocr_jp_out)

Para cada imagen y cada ampliación imprime: tiempo, texto leído y confianza por línea.
En CARPETA guarda una imagen con las cajas dibujadas y `reporte.txt` (UTF-8: ábrelo ahí si la
consola no muestra bien los caracteres japoneses).

Modelo principal: PP-OCRv6 (el que ya usa la app), que incluye japonés. Con --compare se prueba
también el modelo específico japonés PP-OCRv4; si no se puede descargar, se omite y se avisa.
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
FONTS = [  # para --demo: la primera que exista
    "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
DEMO_LINES = ["勇者よ、よく来たな。", "この扉を開けるには、鍵が必要だ。", "村へ戻って、長老に話を聞いてみよう。"]


def read_image(path: Path):
    """cv2.imread no abre rutas con caracteres no ASCII en Windows; así sí."""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def write_image(path: Path, img):
    ok, buf = cv2.imencode(path.suffix or ".png", img)
    if ok:
        buf.tofile(str(path))


def make_demo(path: Path) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Para --demo hace falta Pillow:  pip install pillow")
        return False
    font_path = next((f for f in FONTS if Path(f).exists()), None)
    if font_path is None:
        print("No encontré una fuente japonesa para el ejemplo. Usa tus propias capturas.")
        return False
    font = ImageFont.truetype(font_path, 34)
    img = Image.new("RGB", (900, 260), (28, 30, 44))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(DEMO_LINES):
        draw.text((30, 25 + i * 70), line, font=font, fill=(245, 245, 245))
    img.save(path)
    return True


def load_engines(compare: bool):
    """{nombre: motor}. El modelo v6 es el principal; el v4 japonés solo se añade si se pide."""
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    specs = {"v6": {"Rec.lang_type": LangRec.JAPAN, "Rec.ocr_version": OCRVersion.PPOCRV6}}
    if compare:
        specs["v4 japonés"] = {"Rec.lang_type": LangRec.JAPAN, "Rec.ocr_version": OCRVersion.PPOCRV4,
                               "Rec.model_type": ModelType.MOBILE}
    engines = {}
    warm = np.full((80, 320, 3), 255, np.uint8)       # con texto, para que cargue el reconocedor
    cv2.putText(warm, "TEXT 123", (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3, cv2.LINE_AA)
    for name, params in specs.items():
        try:
            engine = RapidOCR(params=params)
            engine(warm)                     # el reconocedor se carga en la 1ª llamada con texto
            engines[name] = engine
        except Exception as exc:  # noqa: BLE001
            if name == "v6":
                raise
            print(f"  (se omite «{name}»: {str(exc)[:120]})")
    return engines


def run(engine, img, scale):
    big = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else img
    t0 = time.perf_counter()
    out = engine(big)
    elapsed = time.perf_counter() - t0
    txts = list(out.txts or ())
    scores = [float(s) for s in (out.scores or ())]
    boxes = [] if out.boxes is None else [np.asarray(b, float) / scale for b in out.boxes]
    return txts, scores, boxes, elapsed


def draw_boxes(img, boxes):
    vis = img.copy()
    for box in boxes:
        cv2.polylines(vis, [box.astype(np.int32).reshape(-1, 1, 2)], True, (0, 200, 255), 2)
    return vis


_STRIP = {}


def strip_reader():
    """Lector de franjas (OcrEngine.read_strip) para encontrar furigana que el detector no ve.
    None si no se puede crear (entonces el modo furigana usa solo lo que detectó el OCR)."""
    if "reader" not in _STRIP:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from ocr_engine import OcrEngine
            engine = OcrEngine()
            _STRIP["reader"] = lambda img, window: engine.read_strip(img, window)
        except Exception as exc:  # noqa: BLE001
            print(f"      (sin relleno por franjas: {exc})")
            _STRIP["reader"] = None
    return _STRIP["reader"]


def show_modes(txts, scores, boxes, modes, say, img=None):
    """Muestra el texto procesado por japanese.py en cada modo (unión de fragmentos, correcciones,
    furigana). `boxes`: cajas de 4 puntos ya en píxeles de la imagen original `img`."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from Translator.japanese import Mode, process
        from ocr_engine import OcrResult
    except ImportError as exc:
        say(f"      (no se pudo cargar japanese.py: {exc})")
        return
    bboxes = [(float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 0].max()), float(b[:, 1].max()))
              for b in boxes]
    raw = OcrResult("\n".join(txts), list(zip(txts, scores)), bboxes)
    for mode in modes:
        reader = strip_reader() if mode == "furigana" and img is not None else None
        res = process(raw, Mode(mode), img=img, reader=reader)
        extra = " (+ lectura de franjas)" if reader else ""
        say(f"    -> modo {mode}{extra}: {len(res.lines)} línea(s)")
        for text, score in res.lines:
            say(f"         {score:.0%}  {text}")
        for start, end, reading in res.readings:
            say(f"         lectura: {res.text[start:end]} = {reading}")


def analyze(name, img, engines, scales, say, out_dir, stem, modes=()):
    """Lee `img` con cada motor y ampliación, imprime el resultado y guarda las cajas."""
    say(f"\n=== {name} ===")
    say(f"  tamaño: {img.shape[1]}x{img.shape[0]}")
    best = None
    for engine_name, engine in engines.items():
        for scale in scales:
            txts, scores, boxes, elapsed = run(engine, img, scale)
            avg = sum(scores) / len(scores) if scores else 0.0
            say(f"  [{engine_name} x{scale:g}] {len(txts)} línea(s), confianza media {avg:.0%}, {elapsed:.2f} s")
            for t, s in zip(txts, scores):
                say(f"      {s:.0%}  {t}")
            if engine_name == "v6" and modes and txts:
                show_modes(txts, scores, boxes, modes, say, img)
            if engine_name == "v6" and (best is None or avg * len(txts) > best[0]):
                best = (avg * len(txts), boxes, scale)
    if best and best[1]:
        write_image(out_dir / f"{stem}_cajas.png", draw_boxes(img, best[1]))


def capture_regions(delay: int):
    """Genera recortes de pantalla (BGR): cada vez que pulsas Enter, cuenta atrás, pantalla
    congelada y arrastras para elegir la región. Termina con «q»."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PyQt5.QtCore import QEventLoop
        from PyQt5.QtWidgets import QApplication
        from UI.capture import grab_screen
        from UI.overlay import CaptureOverlay
    except ImportError as exc:
        print(f"El modo --capture necesita PyQt5 y los archivos del proyecto (capture.py, overlay.py): {exc}")
        return
    app = QApplication.instance() or QApplication([])
    overlay = CaptureOverlay()
    while True:
        if input("\nEnter = nueva captura (q + Enter = terminar): ").strip().lower().startswith("q"):
            return
        for left in range(delay, 0, -1):
            print(f"  captura en {left}... (cambia al juego)", end="\r", flush=True)
            time.sleep(1)
        print(" " * 50, end="\r")
        got = {}
        loop = QEventLoop()
        overlay.region_selected.connect(lambda crop: (got.update(crop=crop), overlay.dismiss()))
        overlay.closed.connect(loop.quit)
        overlay.open(grab_screen())
        loop.exec_()
        overlay.region_selected.disconnect()
        overlay.closed.disconnect()
        if "crop" in got:
            yield got["crop"]
        else:
            print("  Selección cancelada.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="imágenes o carpetas")
    ap.add_argument("--demo", action="store_true", help="generar y leer una imagen de ejemplo")
    ap.add_argument("--scales", default="1,2", help="ampliaciones separadas por comas (1,2)")
    ap.add_argument("--modes", default="normal,furigana",
                    help="modos de japanese.py a mostrar: normal,furigana,kana (por defecto normal,furigana)")
    ap.add_argument("--capture", action="store_true", help="modo recortes de pantalla")
    ap.add_argument("--delay", type=int, default=3, help="segundos de cuenta atrás en --capture")
    ap.add_argument("--compare", action="store_true", help="probar también el modelo japonés v4")
    ap.add_argument("--out", default="ocr_jp_out")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")        # la consola de Windows no suele ser UTF-8
    except (AttributeError, ValueError):
        pass

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for p in map(Path, args.paths):
        files += sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT) if p.is_dir() else [p]
    if args.demo:
        demo = out_dir / "demo.png"
        if make_demo(demo):
            files.append(demo)
    if not files and not args.capture:
        ap.print_help()
        return 1
    try:
        scales = [float(s) for s in args.scales.split(",") if s.strip()]
    except ValueError:
        print("--scales debe ser una lista de números, por ejemplo 1,2")
        return 1

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if any(m not in ("normal", "furigana", "kana") for m in modes):
        print("--modes solo admite: normal, furigana, kana")
        return 1

    print("Cargando OCR (la primera vez puede descargar modelos)...")
    engines = load_engines(args.compare)

    report = []

    def say(line=""):
        print(line)
        report.append(line)

    report_file = out_dir / "reporte.txt"
    previous = ""                                      # el reporte de sesiones anteriores se conserva
    if report_file.exists():
        previous = report_file.read_text(encoding="utf-8").rstrip() + "\n\n######## nueva sesión ########\n"

    def save_report():
        report_file.write_text(previous + "\n".join(report), encoding="utf-8")

    for f in files:
        img = read_image(f)
        if img is None:
            say(f"\n=== {f.name} ===\n  No se pudo abrir la imagen.")
            continue
        analyze(f.name, img, engines, scales, say, out_dir, f.stem, modes)
    save_report()

    if args.capture:
        count = max((int(p.stem.split("_")[1]) for p in out_dir.glob("captura_[0-9][0-9][0-9].png")),
                    default=0)                         # sigue la numeración: no pisa recortes anteriores
        for crop in capture_regions(max(0, args.delay)):
            count += 1
            stem = f"captura_{count:03d}"
            write_image(out_dir / f"{stem}.png", crop)
            analyze(f"{stem}.png (recorte de pantalla)", crop, engines, scales, say, out_dir, stem, modes)
            save_report()                              # se guarda tras cada captura

    save_report()
    print(f"\nListo. Imágenes con cajas y reporte.txt en: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())