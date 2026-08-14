"""
Servicio de feedback persistente para mejora continua de las recomendaciones.

Los usuarios de la comunidad califican cada recomendación (útil / no útil).
Con esos datos el sistema aprende qué señales funcionan mejor para la comunidad
y las prioriza. Persistencia en JSON local (suficiente para una comunidad
privada de hasta ~100 personas, sin base de datos externa).
"""
import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional
from app.services.storage import atomic_write_json
from app.services.supabase import supabase_store

FEEDBACK_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "feedback.json"


class FeedbackStore:
    """Almacén de votos con persistencia en JSON y bloqueo de escritura."""

    def __init__(self, path: Path = FEEDBACK_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = self._load()
        if supabase_store.enabled:
            self._data = supabase_store.load("feedback", self._data)

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"votes": {}, "signal_scores": {}, "updated_at": None}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"votes": {}, "signal_scores": {}, "updated_at": None}

    def _save(self):
        self._data["updated_at"] = time.time()
        atomic_write_json(self.path, self._data)
        supabase_store.save("feedback", self._data)

    def vote(self, signal_type: str, useful: bool, wallet: Optional[str] = None) -> Dict[str, Any]:
        """Registra un voto sobre una señal. Retorna el score actualizado."""
        with self._lock:
            votes = self._data.setdefault("votes", {})
            signal = votes.setdefault(signal_type, {"useful": 0, "not_useful": 0, "total": 0, "wallets": {}})

            key = "useful" if useful else "not_useful"
            signal[key] += 1
            signal["total"] += 1
            if wallet:
                signal.setdefault("wallets", {})[wallet] = key

            score = self._score_for(signal)
            self._data.setdefault("signal_scores", {})[signal_type] = score
            self._save()
            return {"signal_type": signal_type, "useful": signal["useful"], "not_useful": signal["not_useful"], "score": score}

    @staticmethod
    def _score_for(signal: Dict[str, Any]) -> float:
        """Score 0..1: proporción de votos 'útil' (mín. 3 votos para ser confiable)."""
        total = signal.get("total", 0)
        if total == 0:
            return 0.5  # neutro hasta tener datos
        useful = signal.get("useful", 0)
        base = useful / total
        # Corrección para muestras pequeñas (regresión a la media)
        if total < 3:
            base = base * 0.5 + 0.5 * 0.5
        return round(base, 3)

    def signal_scores(self) -> Dict[str, float]:
        """Scores aprendidos de cada señal (0..1)."""
        with self._lock:
            return dict(self._data.get("signal_scores", {}))

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            votes = self._data.get("votes", {})
            total_votes = sum(v.get("total", 0) for v in votes.values())
            return {
                "total_votes": total_votes,
                "signals": len(votes),
                "signal_scores": self._data.get("signal_scores", {}),
                "updated_at": self._data.get("updated_at"),
            }

    def reset(self):
        with self._lock:
            self._data = {"votes": {}, "signal_scores": {}, "updated_at": None}
            self._save()


# Instancia global compartida por toda la app
feedback_store = FeedbackStore()
