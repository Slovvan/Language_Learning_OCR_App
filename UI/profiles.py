"""Perfiles por juego: nombre -> región fija, idioma, modo de OCR, opciones del modo en vivo y tecla de pausa.
Se guardan como JSON en el ajuste «profiles»."""
import json

REGION_KEYS = ("box", "rect")


def _is_rect(v) -> bool:
    return isinstance(v, list) and len(v) == 4 and all(isinstance(x, int) for x in v)


def _clean(profile: dict):
    """Perfil limpio (solo claves conocidas y válidas) o None."""
    if not isinstance(profile, dict):
        return None
    out = {}
    if "box" in profile or "rect" in profile:
        box, rect = profile.get("box"), profile.get("rect")
        if not (_is_rect(box) and _is_rect(rect) and box[2] > box[0] and box[3] > box[1] and rect[2] > 0 and rect[3] > 0):
            return None
        out["box"], out["rect"] = box, rect          # box: x0 y0 x1 y1 (px reales) · rect: x y ancho alto (px lógicos)
    if isinstance(profile.get("lang"), str):
        out["lang"] = profile["lang"]
    if isinstance(profile.get("mode"), str):
        out["mode"] = profile["mode"]
    if isinstance(profile.get("options"), dict):
        out["options"] = {str(k): bool(v) for k, v in profile["options"].items()}
    if isinstance(profile.get("pause"), dict):
        p = profile["pause"]
        out["pause"] = {"key": str(p.get("key", ""))}
    return out


def load(settings) -> dict:
    try:
        data = json.loads(str(settings.value("profiles", "{}")))
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for name, profile in data.items():
        clean = _clean(profile)
        if clean is not None and str(name).strip():
            out[str(name)] = clean
    return out


def _store(settings, data: dict):
    settings.setValue("profiles", json.dumps(data, ensure_ascii=False))


def save(settings, name: str, profile: dict) -> bool:
    name = (name or "").strip()
    clean = _clean(profile)
    if not name or clean is None:
        return False
    data = load(settings)
    data[name] = clean
    _store(settings, data)
    return True


def delete(settings, name: str) -> bool:
    data = load(settings)
    if name not in data:
        return False
    del data[name]
    _store(settings, data)
    return True


def rename(settings, old: str, new: str) -> bool:
    new = (new or "").strip()
    data = load(settings)
    if old not in data or not new or (new != old and new in data):
        return False
    data = {(new if n == old else n): p for n, p in data.items()}
    _store(settings, data)
    return True
