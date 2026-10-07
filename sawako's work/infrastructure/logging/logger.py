"""
kaoruko/infrastructure/logging/logger.py

Production-grade structured logging system.
- structlog for structured JSON output (files)
- rich for beautiful colored console output
- Log rotation (10MB files, 7 day retention)
- Async-safe
- Automatic log scrubbing for sensitive data
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path
from typing import Any, Optional

import structlog
from rich.console import Console
from rich.logging import RichHandler

# ── Sensitive data patterns to scrub from logs ────────────────────────────────
_SCRUB_PATTERNS = [
    (re.compile(r'"api_key"\s*:\s*"[^"]{8,}"'), '"api_key": "***"'),
    (re.compile(r'"password"\s*:\s*"[^"]*"'), '"password": "***"'),
    (re.compile(r'"pin"\s*:\s*"[^"]*"'), '"pin": "***"'),
    (re.compile(r'"token"\s*:\s*"[^"]{8,}"'), '"token": "***"'),
    (re.compile(r'sk-[a-zA-Z0-9]{40,}'), 'sk-***'),
    (re.compile(r'Bearer [a-zA-Z0-9\-._~+/]{20,}'), 'Bearer ***'),
]


class ScrubFilter(logging.Filter):
    """Scrub sensitive data from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern, replacement in _SCRUB_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        return True


def setup_logging(
    log_dir: Optional[Path] = None,
    log_level: str = "INFO",
    json_logs: bool = True,
    console_logs: bool = True,
) -> None:
    """
    Configure the global logging infrastructure.
    Call once at application startup.

    Args:
        log_dir:      Directory for log files. Defaults to ./data/logs/
        log_level:    Minimum log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        json_logs:    Write structured JSON logs to file
        console_logs: Write colored logs to console via Rich
    """
    if log_dir is None:
        log_dir = Path.cwd() / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # ── Standard library logging configuration ────────────────────────────────
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Suppress noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore", "asyncio", "PIL", "torch"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    handlers: list[logging.Handler] = []

    # ── Console handler (Rich) ────────────────────────────────────────────────
    if console_logs:
        console = Console(stderr=True, highlight=True)
        rich_handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
            tracebacks_show_locals=False,
        )
        rich_handler.setFormatter(logging.Formatter("%(message)s"))
        rich_handler.addFilter(ScrubFilter())
        handlers.append(rich_handler)

    # ── File handler (JSON, rotating) ─────────────────────────────────────────
    if json_logs:
        json_handler = logging.handlers.RotatingFileHandler(
            filename=log_dir / "kaoruko.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=7,
            encoding="utf-8",
        )
        json_handler.addFilter(ScrubFilter())
        handlers.append(json_handler)

    # ── Error-only file handler ───────────────────────────────────────────────
    error_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "kaoruko_errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.addFilter(ScrubFilter())
    handlers.append(error_handler)

    for handler in handlers:
        root_logger.addHandler(handler)

    # ── structlog configuration ───────────────────────────────────────────────
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(component: str, **context: Any) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger bound to a component name.

    Usage:
        log = get_logger("voice.stt")
        log.info("transcript_ready", text="Open Chrome", confidence=0.97)
        log.error("stt_failed", error=str(e), engine="whisper")

    Args:
        component: Dotted component path, e.g. "nlu.intent_classifier"
        **context: Additional context bound to all log calls from this logger

    Returns:
        structlog BoundLogger instance
    """
    logger = structlog.get_logger(component)
    if context:
        logger = logger.bind(**context)
    return logger


def bind_request_context(session_id: str, user_id: Optional[str] = None) -> None:
    """
    Bind request-level context to all subsequent logs in this async context.
    Call at the start of each voice session.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        session_id=session_id,
        **({"user_id": user_id} if user_id else {}),
    )


def clear_request_context() -> None:
    """Clear request-level context (call at end of session)."""
    structlog.contextvars.clear_contextvars()
