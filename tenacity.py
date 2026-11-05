"""
tenacity fallback shim.
"""
from __future__ import annotations
from typing import Any


class AsyncRetrying:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def retry_if_exception_type(*args: Any, **kwargs: Any) -> Any:
    return lambda *a, **k: True


def stop_never(*args: Any, **kwargs: Any) -> Any:
    return lambda *a, **k: False


def wait_exponential(*args: Any, **kwargs: Any) -> Any:
    return lambda *a, **k: 0

