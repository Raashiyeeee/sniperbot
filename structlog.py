"""
structlog fallback shim.
Provides a lightweight logging adapter that supports .bind() and key-value kwargs.
"""
from __future__ import annotations

import logging
from typing import Any, Dict


class _BoundLogger:
    def __init__(self, logger: logging.Logger, context: Dict[str, Any] | None = None) -> None:
        self._logger = logger
        self._context = context or {}

    def bind(self, **kwargs: Any) -> "_BoundLogger":
        new_ctx = dict(self._context)
        new_ctx.update(kwargs)
        return _BoundLogger(self._logger, new_ctx)

    def _format(self, event: str, kwargs: Dict[str, Any]) -> str:
        all_kw = dict(self._context)
        all_kw.update(kwargs)
        if not all_kw:
            return event
        kw_str = " ".join(f"{k}={v}" for k, v in all_kw.items())
        return f"{event} | {kw_str}"

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(self._format(event, kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(self._format(event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(self._format(event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(self._format(event, kwargs))

    def critical(self, event: str, **kwargs: Any) -> None:
        self._logger.critical(self._format(event, kwargs))

    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(self._format(event, kwargs))


def get_logger(name: str = "root") -> _BoundLogger:
    return _BoundLogger(logging.getLogger(name))


def configure(*args: Any, **kwargs: Any) -> None:
    logging.basicConfig(level=logging.INFO)


class _MockModule:
    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: None


processors = _MockModule()
stdlib = _MockModule()
dev = _MockModule()

