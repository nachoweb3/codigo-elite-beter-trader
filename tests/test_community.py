"""Tests del servicio de comunidad (snapshots, percentiles, evolución)."""
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.services.community import CommunityStore, _wallet_id


@pytest.fixture
def store(tmp_path):
    """Store aislado en directorio temporal."""
    return CommunityStore(path=tmp_path / "community.json")


def test_wallet_id_is_anonymous_and_stable():
    a = _wallet_id("5rqBo8rnbcGW6XTqRDpCZ6cAPpZjwYR9t5z4MMh1YRQ4")
    b = _wallet_id("5rqBo8rnbcGW6XTqRDpCZ6cAPpZjwYR9t5z4MMh1YRQ4")
    c = _wallet_id("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert a == b  # mismo wallet -> mismo id
    assert a != c  # distinto wallet -> distinto id
    assert len(a) == 16  # hash corto
    # No contiene la dirección original
    assert "5rqBo" not in a


def test_record_analysis_and_evolution(store):
    res = store.record_analysis(
        "walletA",
        {"win_rate": 40, "profit_factor": 1.5, "total_pnl": 1.2, "total_trades": 30, "total_volume": 10},
        profile={"id": "swing"},
        tokens_count=12,
    )
    assert res["points"] == 1

    # Segundo análisis -> evolucion con 2 puntos
    store.record_analysis(
        "walletA",
        {"win_rate": 45, "profit_factor": 1.8, "total_pnl": 2.5, "total_trades": 35, "total_volume": 12},
    )
    ev = store.evolution("walletA")
    assert len(ev["points"]) == 2
    assert ev["points"][-1]["pnl"] == 2.5


def test_benchmark_percentiles(store):
    # 3 wallets distintas: la tercera es la mejor en win rate
    store.record_analysis("w1", {"win_rate": 20, "profit_factor": 0.5, "total_pnl": -1, "total_trades": 10})
    store.record_analysis("w2", {"win_rate": 30, "profit_factor": 1.0, "total_pnl": 0, "total_trades": 20})
    store.record_analysis("w3", {"win_rate": 50, "profit_factor": 2.0, "total_pnl": 3, "total_trades": 40})

    res = store.benchmark("w3")
    assert res["available"] is True
    assert res["community_size"] == 3
    # w3 (50%) es mejor que w1 (20%) y w2 (30%) -> 100 percentil
    assert res["percentiles"]["win_rate"] == 100.0
    # w3 tiene 40 trades, mejor que 10 y 20 -> 100
    assert res["percentiles"]["total_trades"] == 100.0

    # w1 (20%) es peor que las otras dos -> 0 percentil
    res1 = store.benchmark("w1")
    assert res1["percentiles"]["win_rate"] == 0.0

    # Wallet nunca analizada -> no disponible
    res_unknown = store.benchmark("unknown_wallet")
    assert res_unknown["available"] is False


def test_community_stats(store):
    store.record_analysis("w1", {"win_rate": 20, "profit_factor": 0.5, "total_pnl": -1, "total_trades": 10})
    store.record_analysis("w2", {"win_rate": 40, "profit_factor": 1.5, "total_pnl": 2, "total_trades": 20})
    stats = store.community_stats()
    assert stats["wallets"] == 2
    assert stats["avg_win_rate"] == 30.0
    assert stats["avg_pnl"] == 0.5


def test_persistence(tmp_path):
    path = tmp_path / "community.json"
    s1 = CommunityStore(path=path)
    s1.record_analysis("w1", {"win_rate": 50, "profit_factor": 1, "total_pnl": 1, "total_trades": 5})
    # Nueva instancia -> datos cargados de disco
    s2 = CommunityStore(path=path)
    ev = s2.evolution("w1")
    assert len(ev["points"]) == 1
    assert s2.benchmark("w1")["available"] is True
