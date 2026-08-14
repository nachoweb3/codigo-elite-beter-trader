"""Persistencia opcional en Supabase usando su API REST.

La clave service role solo se usa en el backend y nunca se envía al navegador.
Cuando SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no están configuradas, los
servicios continúan usando sus archivos JSON locales.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.config import get_settings


class SupabaseStore:
    """Almacén sencillo de pares clave/JSON para despliegues sin disco persistente."""

    def __init__(self, url: str = "", service_role_key: str = ""):
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key
        self.endpoint = f"{self.url}/rest/v1/app_store" if self.url else ""

    @classmethod
    def from_settings(cls) -> "SupabaseStore":
        settings = get_settings()
        return cls(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.service_role_key)

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }

    def load(self, store_key: str, default: Any) -> Any:
        if not self.enabled:
            return default
        try:
            response = httpx.get(
                f"{self.endpoint}?select=payload&store_key=eq.{quote(store_key, safe='')}",
                headers=self._headers(),
                timeout=5,
            )
            response.raise_for_status()
            rows = response.json()
            return rows[0].get("payload", default) if rows else default
        except Exception:
            # La persistencia local sigue siendo un fallback válido si Supabase
            # está temporalmente caído o todavía no tiene la tabla creada.
            return default

    def save(self, store_key: str, payload: Any) -> bool:
        if not self.enabled:
            return False
        try:
            response = httpx.post(
                self.endpoint,
                headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                json={"store_key": store_key, "payload": payload},
                timeout=5,
            )
            response.raise_for_status()
            return True
        except Exception:
            return False


supabase_store = SupabaseStore.from_settings()
