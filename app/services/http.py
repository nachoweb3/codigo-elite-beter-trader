"""Utilidades HTTP: reintentos con backoff exponencial."""
import asyncio
import logging
from typing import Awaitable, Callable, Optional, Tuple, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def with_retries(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 5.0,
    exceptions: Tuple[type, ...] = (Exception,),
    label: str = "request",
) -> T:
    """Ejecuta una corutina con reintentos y backoff exponencial.

    Args:
        coro_factory: Callable que crea la corutina a ejecutar (se vuelve
            a llamar en cada intento para evitar re-await de corutinas).
        retries: Número máximo de intentos (incluye el primero).
        base_delay: Espera inicial en segundos.
        max_delay: Espera máxima en segundos.
        exceptions: Excepciones que se reintentan.
        label: Nombre descriptivo para los logs.

    Raises:
        La última excepción si se agotan los intentos.
    """
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except exceptions as exc:
            attempt += 1
            if attempt >= retries:
                logger.warning(
                    "%s falló tras %d intentos: %s", label, attempt, exc
                )
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            logger.info(
                "Reintentando %s (intento %d/%d) en %.1fs: %s",
                label, attempt + 1, retries, delay, exc,
            )
            await asyncio.sleep(delay)
