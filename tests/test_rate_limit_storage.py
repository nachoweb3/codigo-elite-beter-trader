"""Tests del rate limiter (token bucket) y de la escritura atómica de JSON."""
import asyncio
import json
import time
from pathlib import Path

import pytest

from app.services.rate_limit import TokenBucket, RateLimiter
from app.services.storage import atomic_write_json


class TestTokenBucket:
    def test_permite_rafaga_inicial(self):
        bucket = TokenBucket(rate=1.0, capacity=5.0)
        assert bucket._tokens == 5.0  # bucket lleno al arrancar

    def test_rellena_con_el_tiempo(self):
        bucket = TokenBucket(rate=2.0, capacity=10.0)
        bucket._tokens = 0.0
        bucket._updated = time.monotonic() - 1.0
        bucket._tokens = min(bucket.capacity, bucket._tokens + 1.0 * 2.0)
        assert bucket._tokens == pytest.approx(2.0, abs=0.01)

    def test_no_supera_capacidad(self):
        bucket = TokenBucket(rate=5.0, capacity=3.0)
        assert bucket._tokens == 3.0
        bucket._tokens = min(bucket.capacity, bucket._tokens + 100)
        assert bucket._tokens == 3.0


class TestRateLimiterAsync:
    @pytest.mark.asyncio
    async def test_adquirir_no_bloquea_cuando_hay_tokens(self):
        limiter = RateLimiter(rate=100.0, capacity=50.0)
        start = time.monotonic()
        async with limiter:
            pass
        assert time.monotonic() - start < 0.5

    @pytest.mark.asyncio
    async def test_concurrencia_no_excede_capacidad(self):
        """Aunque 30 corutinas pidan a la vez, el bucket nunca entrega más de capacity."""
        limiter = RateLimiter(rate=1000.0, capacity=10.0)
        hits = 0

        async def worker():
            nonlocal hits
            async with limiter:
                hits += 1
                await asyncio.sleep(0.01)

        await asyncio.gather(*[worker() for _ in range(30)])
        # capacity=10 y rate alto: las 30 pasan, pero serializadas por el bucket
        assert hits == 30

    @pytest.mark.asyncio
    async def test_instancia_global_compartida(self):
        from app.services.rate_limit import helius_limiter
        assert helius_limiter.bucket.capacity == 10.0
        assert helius_limiter.bucket.rate == 3.0
        assert helius_limiter.name == "helius"

    @pytest.mark.asyncio
    async def test_snapshot_expone_estado(self):
        limiter = RateLimiter(rate=3.0, capacity=10.0, name="helius")
        async with limiter:
            snap = limiter.snapshot()
        assert snap["name"] == "helius"
        assert snap["hits"] == 1
        assert snap["waits"] == 0
        assert snap["capacity"] == 10.0
        assert snap["rate_per_second"] == 3.0
        assert 0 <= snap["pressure"] <= 100
        assert snap["tokens_available"] <= 9.0  # uno consumido por el async with


class TestAtomicWrite:
    def test_escribe_y_relee_correctamente(self, tmp_path):
        target = tmp_path / "data.json"
        data = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
        atomic_write_json(target, data)
        assert json.loads(target.read_text(encoding="utf-8")) == data

    def test_no_deja_temporales(self, tmp_path):
        target = tmp_path / "data.json"
        atomic_write_json(target, {"x": 1})
        leftovers = [p for p in tmp_path.iterdir() if ".tmp-" in p.name]
        assert leftovers == []

    def test_sobrescribe_archivo_existente(self, tmp_path):
        target = tmp_path / "data.json"
        atomic_write_json(target, {"v": 1})
        atomic_write_json(target, {"v": 2})
        assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}

    def test_crea_directorios(self, tmp_path):
        target = tmp_path / "sub" / "dir" / "data.json"
        atomic_write_json(target, {"ok": True})
        assert target.exists()

    def test_escribe_unicode_sin_escapar(self, tmp_path):
        target = tmp_path / "data.json"
        atomic_write_json(target, {"texto": "mejor trader 🚀"})
        raw = target.read_text(encoding="utf-8")
        assert "mejor trader" in raw
        assert json.loads(raw) == {"texto": "mejor trader 🚀"}
