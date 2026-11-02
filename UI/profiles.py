"""Perfiles por juego del modo en vivo: nombre -> región fija, idioma, modo de OCR y «unir líneas».
Se guardan como JSON en el ajuste «live.profiles»."""
import json

KEYS = ("box", "rect", "screen", "lang", "mode", "merge")


def load(settings) -> dict:
    try:
        data = json.loads(settings.get("live.profiles"))
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(n): p for n, p in data.items() if isinstance(p, dict) and _valid(p)}


def _valid(p: dict) -> bool:
    box, rect = p.get("box"), p.get("rect")
    return (isinstance(box, list) and len(box) == 4 and all(isinstance(v, int) for v in box)
            and isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, int) for v in rect)
            and box[2] > box[0] and box[3] > box[1])


def _store(settings, data: dict):
    settings.set("live.profiles", json.dumps(data, ensure_ascii=False))


def save(settings, name: str, profile: dict) -> bool:
    name = name.strip()
    profile = {k: profile[k] for k in KEYS if k in profile}
    if not name or not _valid(profile):
        return False
    data = load(settings)
    data[name] = profile
    _store(settings, data)
    return True


def delete(settings, name: str) -> bool:
    data = load(settings)
    if name not in data:
        return False
    del data[name]
    _store(settings, data)
    return True
