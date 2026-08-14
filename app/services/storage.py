"""Escritura atómica de JSON: evita corrupción si el proceso muere a mitad de escritura.

Con una comunidad de hasta 100 personas escribiendo snapshots/votos a la vez,
una escritura interrumpida (crash, corte de luz, dos requests simultáneos)
podía dejar el JSON a medias. Escribir a un archivo temporal y renombrarlo
(rename atómico en el mismo filesystem) garantiza que el archivo final siempre
contenga un JSON completo.
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any) -> None:
    """Escribe `data` como JSON de forma atómica (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=str(path.parent),
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Limpiar el temporal si algo falló antes del rename
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
