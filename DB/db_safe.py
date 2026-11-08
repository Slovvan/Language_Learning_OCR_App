"""Abrir una base de datos del usuario sin que un archivo dañado impida arrancar la app."""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def open_safe(factory, path, notes: list):
    """`factory()` abre la base. Si el archivo está dañado, se aparta (nombre.db.dañada-FECHA: no se borra, se puede
    recuperar a mano), se abre una base nueva y se añade un aviso a `notes`."""
    path = Path(path)
    try:
        return factory()
    except (sqlite3.DatabaseError, OSError) as exc:
        if not path.exists():
            raise
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        aside = path.with_name(f"{path.name}.danada-{stamp}")
        try:
            shutil.move(str(path), str(aside))
            for extra in ("-wal", "-shm", "-journal"):
                side = Path(str(path) + extra)
                if side.exists():
                    side.unlink()
        except OSError:
            raise exc
        notes.append(f"«{path.name}» estaba dañado ({exc}); se apartó como «{aside.name}» y se empezó una nueva.")
        return factory()
