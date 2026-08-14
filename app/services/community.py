"""
Servicio de comunidad: snapshots de análisis y comparación entre wallets.

El objetivo de la comunidad privada (≤100 personas) es que cada usuario vea
cómo está su trading respecto a los demás. Este servicio:

1. Guarda un snapshot anónimo de cada análisis (métricas clave + perfil).
2. Calcula percentiles de cada wallet frente a la comunidad (win rate,
   profit factor, P&L, actividad).
3. Persiste la evolución de P&L/valor de cada wallet en el tiempo, para que
   el gráfico "Evolución de P&L" muestre datos reales del servidor y no solo
   los que guarda el navegador localmente.

Persistencia en JSON local (suficiente para una comunidad privada, igual
que feedback.py). La wallet se guarda con hash para respetar la privacidad
salvo que el usuario decida compartir su dirección.
"""
import hashlib
import json
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List
from app.services.storage import atomic_write_json

COMMUNITY_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "community.json"


def _wallet_id(wallet_address: str) -> str:
    """ID anónimo de la wallet: hash SHA-256 corto (8 chars)."""
    return hashlib.sha256(wallet_address.encode()).hexdigest()[:16]


class CommunityStore:
    """Almacén de snapshots y evolución con persistencia en JSON."""

    def __init__(self, path: Path = COMMUNITY_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"snapshots": {}, "evolution": {}, "updated_at": None}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"snapshots": {}, "evolution": {}, "updated_at": None}

    def _save(self):
        self._data["updated_at"] = time.time()
        # Limitar evolución a 200 puntos por wallet para que el JSON no crezca sin fin
        for wid in self._data.get("evolution", {}):
            ev = self._data["evolution"][wid]
            if len(ev) > 200:
                self._data["evolution"][wid] = ev[-200:]
        atomic_write_json(self.path, self._data)

    def record_analysis(
        self,
        wallet_address: str,
        metrics: Dict[str, Any],
        profile: Optional[Dict[str, Any]] = None,
        tokens_count: int = 0,
        total_value_usd: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Guarda un snapshot del análisis de una wallet (anónimo)."""
        wid = _wallet_id(wallet_address)
        now = time.time()
        with self._lock:
            snapshots = self._data.setdefault("snapshots", {})
            snapshots[wid] = {
                "win_rate": round(metrics.get("win_rate", 0) or 0, 2),
                "profit_factor": round(metrics.get("profit_factor", 0) or 0, 3),
                "total_pnl": round(metrics.get("total_pnl", 0) or 0, 6),
                "total_trades": int(metrics.get("total_trades", 0) or 0),
                "total_volume": round(metrics.get("total_volume", 0) or 0, 4),
                "avg_hold_time_seconds": int(metrics.get("avg_hold_time_seconds", 0) or 0),
                "tokens_count": tokens_count,
                "total_value_usd": round(total_value_usd or 0, 2),
                "profile": ((profile or {}).get("style") or (profile or {}).get("id") or "unknown") if profile else "unknown",
                "updated_at": now,
            }

            # Evolución en el tiempo: un punto por análisis por wallet
            evolution = self._data.setdefault("evolution", {})
            ev = evolution.setdefault(wid, [])
            ev.append({
                "t": now,
                "pnl": round(metrics.get("total_pnl", 0) or 0, 6),
                "win_rate": round(metrics.get("win_rate", 0) or 0, 2),
                "value_usd": round(total_value_usd or 0, 2),
                "trades": int(metrics.get("total_trades", 0) or 0),
            })

            self._save()
            return {"wallet_id": wid, "recorded_at": now, "points": len(ev)}

    def benchmark(self, wallet_address: str) -> Dict[str, Any]:
        """Percentiles de la wallet frente a toda la comunidad analizada."""
        wid = _wallet_id(wallet_address)
        with self._lock:
            snapshots = self._data.get("snapshots", {})
            mine = snapshots.get(wid)
            others = [v for k, v in snapshots.items() if k != wid]
            if not mine:
                return {
                    "available": False,
                    "message": "Aún no hay datos de esta wallet. Analízala para ver cómo te comparas con la comunidad.",
                    "percentiles": {},
                }

            def percentile(value, key):
                if value is None:
                    return None
                pool = [o.get(key, 0) or 0 for o in others]
                if not pool:
                    return None
                below = sum(1 for v in pool if v <= value)
                return round(below / len(pool) * 100, 1)

            pct = {
                "win_rate": percentile(mine.get("win_rate"), "win_rate"),
                "profit_factor": percentile(mine.get("profit_factor"), "profit_factor"),
                "total_pnl": percentile(mine.get("total_pnl"), "total_pnl"),
                "total_trades": percentile(mine.get("total_trades"), "total_trades"),
            }

            return {
                "available": True,
                "wallet_id": wid,
                "community_size": len(snapshots),
                "mine": mine,
                "percentiles": pct,
            }

    def evolution(self, wallet_address: str, limit: int = 60) -> Dict[str, Any]:
        """Historial de P&L/valor de la wallet persistido en el servidor."""
        wid = _wallet_id(wallet_address)
        with self._lock:
            ev = self._data.get("evolution", {}).get(wid, [])
            return {"wallet_id": wid, "points": ev[-limit:]}

    def community_stats(self) -> Dict[str, Any]:
        """Estadísticas agregadas de la comunidad (para el ranking)."""
        with self._lock:
            snapshots = self._data.get("snapshots", {})
            if not snapshots:
                return {"wallets": 0, "avg_win_rate": 0, "avg_pnl": 0, "avg_profit_factor": 0}
            n = len(snapshots)
            return {
                "wallets": n,
                "avg_win_rate": round(sum(s.get("win_rate", 0) for s in snapshots.values()) / n, 2),
                "avg_pnl": round(sum(s.get("total_pnl", 0) for s in snapshots.values()) / n, 6),
                "avg_profit_factor": round(sum(s.get("profit_factor", 0) for s in snapshots.values()) / n, 3),
                "avg_trades": round(sum(s.get("total_trades", 0) for s in snapshots.values()) / n, 1),
                "profiles": {p: sum(1 for s in snapshots.values() if s.get("profile") == p) for p in
                             set(s.get("profile") for s in snapshots.values())},
            }

    def reset(self):
        with self._lock:
            self._data = {"snapshots": {}, "evolution": {}, "updated_at": None}
            self._save()


# Instancia global compartida por toda la app
community_store = CommunityStore()
