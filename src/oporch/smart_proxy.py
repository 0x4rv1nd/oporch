"""oporch built-in smart proxy — rate-limit retry + model fallback + concurrency guard.

Operates at the EXECUTOR level (not HTTP): wraps ``OpenCodeAgentExecutor``.
When opencode's subprocess output signals a rate limit or quota error:

1. **Rate limit (429 / throttle)** → exponential backoff + jitter → retry same model
2. **Quota exceeded (billing/credits)** → switch to configured fallback model → retry
3. **Per-model concurrency guard** → asyncio.Semaphore per model_id prevents burst
4. **Headroom detection** → if Headroom is already running on port 8787 this wrapper
   transparently delegates and adds zero overhead.

Usage::

    executor = make_smart_executor()       # auto-detects Headroom
    executor = RetryingOpenCodeExecutor(base)  # explicit wrapping

Signals detected
----------------
Rate limit (transient — retry with backoff)::
    429, rate_limit_error, ratelimiterror, too many requests,
    rate limit exceeded, throttl, retry after, x-ratelimit

Quota exceeded (persistent — switch model)::
    insufficient_quota, quota_exceeded, exceeded your current quota,
    you have run out, no credits, out of credits, payment required, 402
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import time
from io import StringIO
from typing import Any

from .executor import call_executor_async

logger = logging.getLogger(__name__)

# ── Signal patterns ────────────────────────────────────────────────────────────
_RATE_LIMIT_SIGNALS: tuple[str, ...] = (
    "429",
    "rate_limit_error",
    "ratelimiterror",
    "too many requests",
    "rate limit exceeded",
    "throttl",
    "retry after",
    "x-ratelimit",
)

_QUOTA_SIGNALS: tuple[str, ...] = (
    "insufficient_quota",
    "quota_exceeded",
    "exceeded your current quota",
    "you have run out",
    "no credits",
    "out of credits",
    "payment required",
    "402",
    "billing",
)

_MAX_CONCURRENT_PER_MODEL = 3
_model_semaphores: dict[str, asyncio.Semaphore] = {}


# ── Helpers ────────────────────────────────────────────────────────────────────
def headroom_running(port: int = 8787) -> bool:
    """Return True if Headroom proxy is already listening on *port*."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def _detect(text: str, signals: tuple[str, ...]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(s in low for s in signals)


def _backoff_secs(attempt: int, base: float = 2.0, cap: float = 60.0) -> float:
    """Capped exponential backoff with ±20 % jitter."""
    wait = min(base * (2 ** attempt), cap)
    jitter = wait * 0.2 * (random.random() * 2 - 1)
    return max(1.0, wait + jitter)


def _sem_for(model_id: str) -> asyncio.Semaphore:
    if model_id not in _model_semaphores:
        _model_semaphores[model_id] = asyncio.Semaphore(_MAX_CONCURRENT_PER_MODEL)
    return _model_semaphores[model_id]


# ── Stats ──────────────────────────────────────────────────────────────────────
class ProxyStats:
    """Accumulates per-model token usage and retry/fallback events."""

    def __init__(self) -> None:
        self.tokens_in: dict[str, int] = {}
        self.tokens_out: dict[str, int] = {}
        self.retries: dict[str, int] = {}
        self.fallbacks: dict[str, int] = {}
        self.calls: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def record(
        self,
        model: str,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        retry: bool = False,
        fallback: bool = False,
    ) -> None:
        async with self._lock:
            self.tokens_in[model] = self.tokens_in.get(model, 0) + tokens_in
            self.tokens_out[model] = self.tokens_out.get(model, 0) + tokens_out
            self.calls[model] = self.calls.get(model, 0) + 1
            if retry:
                self.retries[model] = self.retries.get(model, 0) + 1
            if fallback:
                self.fallbacks[model] = self.fallbacks.get(model, 0) + 1

    def as_rich_table(self) -> "Table":  # type: ignore[name-defined]
        from rich.table import Table

        table = Table(title="⚡ Proxy Stats", show_header=True, header_style="bold cyan")
        table.add_column("Model", style="white")
        table.add_column("Calls", justify="right")
        table.add_column("Tokens In", justify="right")
        table.add_column("Tokens Out", justify="right")
        table.add_column("Retries", justify="right", style="yellow")
        table.add_column("Fallbacks", justify="right", style="red")
        for model in sorted(set(self.calls)):
            table.add_row(
                model,
                str(self.calls.get(model, 0)),
                str(self.tokens_in.get(model, 0)),
                str(self.tokens_out.get(model, 0)),
                str(self.retries.get(model, 0)),
                str(self.fallbacks.get(model, 0)),
            )
        return table

    def is_empty(self) -> bool:
        return not self.calls


# Global singleton — shared across all executor instances in a process.
proxy_stats = ProxyStats()


# ── Core executor wrapper ──────────────────────────────────────────────────────
class RetryingOpenCodeExecutor:
    """Wraps any AgentExecutor with rate-limit retry, model fallback, and
    per-model concurrency limiting.

    Transparently passes through when Headroom is detected on *headroom_port*.
    """

    def __init__(
        self,
        base: Any,
        *,
        max_retries: int = 4,
        base_backoff: float = 2.0,
        headroom_port: int = 8787,
        check_headroom: bool = True,
    ) -> None:
        self._base = base
        self._max_retries = max_retries
        self._base_backoff = base_backoff
        self._defer_to_headroom = check_headroom and headroom_running(headroom_port)
        if self._defer_to_headroom:
            logger.info(
                "Headroom proxy detected on :%d — built-in retry proxy deferred.",
                headroom_port,
            )

    @property
    def headroom_active(self) -> bool:
        return self._defer_to_headroom

    # ------------------------------------------------------------------
    # AgentExecutor protocol
    # ------------------------------------------------------------------
    def run(self, role: Any, task: Any, context: Any) -> Any:
        """Synchronous path — just delegates (retry requires async)."""
        return self._base.run(role, task, context)

    async def run_async(self, role: Any, task: Any, context: Any) -> Any:
        """Async path with full retry / fallback / concurrency logic."""
        if self._defer_to_headroom:
            return await call_executor_async(self._base, role, task, context)

        from . import config as cfg

        role_key = role.value if hasattr(role, "value") else str(role)
        try:
            model_id = task.model_override or cfg.resolve_model(role_key) or role_key
        except Exception:
            model_id = role_key

        async with _sem_for(model_id):
            return await self._retrying_call(role, task, context, role_key, model_id)

    async def _retrying_call(
        self,
        role: Any,
        task: Any,
        context: Any,
        role_key: str,
        model_id: str,
    ) -> Any:
        from . import config as cfg

        current_model = model_id
        fallback_used = False
        result = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                logger.info(
                    "Proxy retry %d/%d role=%s model=%s",
                    attempt, self._max_retries, role_key, current_model,
                )

            result = await call_executor_async(self._base, role, task, context)

            await proxy_stats.record(
                current_model,
                tokens_in=result.tokens_in or 0,
                tokens_out=result.tokens_out or 0,
            )

            if result.success:
                return result

            combined = f"{result.error or ''} {result.output or ''}"

            # Quota exhausted → one-shot model fallback, no sleep
            if _detect(combined, _QUOTA_SIGNALS) and not fallback_used:
                try:
                    fb = _resolve_fallback(role_key)
                except Exception:
                    fb = None
                if fb and fb != current_model:
                    logger.warning(
                        "Quota exceeded for %s — switching to fallback %s",
                        current_model, fb,
                    )
                    await proxy_stats.record(current_model, fallback=True)
                    current_model = fb
                    task = task.model_copy(update={"model_override": current_model})
                    fallback_used = True
                    continue  # immediate retry with fallback, no sleep

            # Rate-limited → exponential backoff
            if _detect(combined, _RATE_LIMIT_SIGNALS) and attempt < self._max_retries:
                wait = _backoff_secs(attempt, self._base_backoff)
                logger.warning(
                    "Rate limit for %s — sleeping %.1fs (retry %d/%d)",
                    current_model, wait, attempt + 1, self._max_retries,
                )
                await proxy_stats.record(current_model, retry=True)
                await asyncio.sleep(wait)
                continue

            # Non-retryable failure
            return result

        return result  # last result after all retries


# ── Fallback resolution helper ─────────────────────────────────────────────────
def _resolve_fallback(role_key: str) -> str | None:
    """Return the resolved model_id for a role's fallback, or None."""
    from . import config as cfg

    try:
        roles = cfg.load_roles()
        if role_key not in roles.roles:
            return None
        role_cfg = roles.roles[role_key]
        if not role_cfg.fallback:
            return None
        try:
            mcfg = cfg.load_models()
        except Exception:
            return role_cfg.fallback
        if role_cfg.fallback in mcfg.models:
            return mcfg.models[role_cfg.fallback].model_id
        return role_cfg.fallback
    except Exception:
        return None


# ── Factory ────────────────────────────────────────────────────────────────────
def make_smart_executor(
    opencode_cmd: str = "opencode",
    sandbox_env: bool = True,
    *,
    check_headroom: bool = True,
) -> RetryingOpenCodeExecutor:
    """Build a RetryingOpenCodeExecutor wrapping the real OpenCode executor.

    Checks whether Headroom is running first and sets defer mode accordingly.
    """
    from .executor import OpenCodeAgentExecutor

    base = OpenCodeAgentExecutor(opencode_cmd=opencode_cmd, sandbox_env=sandbox_env)
    return RetryingOpenCodeExecutor(base, check_headroom=check_headroom)
