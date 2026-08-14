import sys
from pathlib import Path

# Asegurar que el paquete `app` es importable desde los tests
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
