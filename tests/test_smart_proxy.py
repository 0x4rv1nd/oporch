"""Tests for oporch.smart_proxy — RetryingOpenCodeExecutor."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from oporch.models import AgentTask, AgentResult, ContextPack
from oporch.smart_proxy import (
    RetryingOpenCodeExecutor,
    _detect,
    _backoff_secs,
    headroom_running,
    ProxyStats,
    _RATE_LIMIT_SIGNALS,
    _QUOTA_SIGNALS,
)


# ── _detect ────────────────────────────────────────────────────────────────────

def test_detect_rate_limit():
    assert _detect("Error 429 too many requests", _RATE_LIMIT_SIGNALS)
    assert _detect("RateLimitError: retry after 60s", _RATE_LIMIT_SIGNALS)
    assert _detect("throttled by upstream", _RATE_LIMIT_SIGNALS)
    assert not _detect("Some unrelated error", _RATE_LIMIT_SIGNALS)
    assert not _detect("", _RATE_LIMIT_SIGNALS)


def test_detect_quota():
    assert _detect("insufficient_quota for this model", _QUOTA_SIGNALS)
    assert _detect("You have exceeded your current quota", _QUOTA_SIGNALS)
    assert _detect("out of credits, please top up", _QUOTA_SIGNALS)
    assert not _detect("Some normal error", _QUOTA_SIGNALS)


# ── _backoff_secs ──────────────────────────────────────────────────────────────

def test_backoff_grows():
    waits = [_backoff_secs(i) for i in range(5)]
    # Should generally grow (with jitter, so check median growth)
    for i in range(1, 4):
        assert waits[i] > 1.0  # at minimum 1 second

def test_backoff_capped():
    # At high attempts it should be capped at 60 + jitter
    w = _backoff_secs(20, base=2.0, cap=60.0)
    assert w <= 72.0  # 60 * 1.2 (max jitter)


# ── headroom_running ───────────────────────────────────────────────────────────

def test_headroom_not_running_on_random_port():
    # Port 19999 is almost certainly not in use
    assert not headroom_running(port=19999)


# ── ProxyStats ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_proxy_stats_accumulates():
    stats = ProxyStats()
    await stats.record("gpt-4o", tokens_in=100, tokens_out=50)
    await stats.record("gpt-4o", tokens_in=200, tokens_out=80, retry=True)
    await stats.record("claude-3", tokens_in=300, fallback=True)

    assert stats.tokens_in["gpt-4o"] == 300
    assert stats.tokens_out["gpt-4o"] == 130
    assert stats.retries["gpt-4o"] == 1
    assert stats.fallbacks["claude-3"] == 1
    assert not stats.is_empty()


def test_proxy_stats_empty():
    stats = ProxyStats()
    assert stats.is_empty()


# ── RetryingOpenCodeExecutor ───────────────────────────────────────────────────

def _make_task() -> AgentTask:
    return AgentTask(
        objective="Test task",
        work_unit_id="WU-001",
        acceptance_criteria=["pass"],
    )

def _make_ctx() -> ContextPack:
    return ContextPack()


@pytest.mark.asyncio
async def test_pass_through_when_headroom_active():
    """When Headroom is detected, delegate without retry logic."""
    mock_base = MagicMock()
    mock_result = AgentResult(success=True, output="done")

    with patch("oporch.smart_proxy.headroom_running", return_value=True), \
         patch("oporch.smart_proxy.call_executor_async", return_value=mock_result) as mock_call:
        executor = RetryingOpenCodeExecutor(mock_base, check_headroom=True)
        assert executor.headroom_active is True
        result = await executor.run_async("builder", _make_task(), _make_ctx())
        assert result.success
        mock_call.assert_called_once()


@pytest.mark.asyncio
async def test_success_on_first_attempt():
    """No retry when the first call succeeds."""
    mock_base = MagicMock()
    success_result = AgentResult(success=True, output="done")

    call_count = 0

    async def fake_call(executor, role, task, context):
        nonlocal call_count
        call_count += 1
        return success_result

    with patch("oporch.smart_proxy.headroom_running", return_value=False), \
         patch("oporch.smart_proxy.call_executor_async", side_effect=fake_call), \
         patch("oporch.config.resolve_model", return_value="test-model"):
        executor = RetryingOpenCodeExecutor(mock_base, check_headroom=False)
        result = await executor.run_async("builder", _make_task(), _make_ctx())
        assert result.success
        assert call_count == 1


@pytest.mark.asyncio
async def test_rate_limit_retries_with_backoff():
    """429 errors trigger retry with backoff."""
    mock_base = MagicMock()
    rate_limit_result = AgentResult(
        success=False, error="429 too many requests — rate limit exceeded"
    )
    success_result = AgentResult(success=True, output="done after retry")

    call_count = 0

    async def fake_call(executor, role, task, context):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return rate_limit_result
        return success_result

    with patch("oporch.smart_proxy.headroom_running", return_value=False), \
         patch("oporch.smart_proxy.call_executor_async", side_effect=fake_call), \
         patch("oporch.smart_proxy._backoff_secs", return_value=0.01), \
         patch("oporch.config.resolve_model", return_value="test-model"):
        executor = RetryingOpenCodeExecutor(mock_base, check_headroom=False, max_retries=4)
        result = await executor.run_async("builder", _make_task(), _make_ctx())
        assert result.success
        assert call_count == 3


@pytest.mark.asyncio
async def test_quota_exhausted_triggers_fallback():
    """Quota exceeded triggers one-shot model fallback, no sleep."""
    mock_base = MagicMock()
    quota_result = AgentResult(
        success=False, error="insufficient_quota for this billing period"
    )
    fallback_result = AgentResult(success=True, output="done with fallback")

    models_used = []

    async def fake_call(executor, role, task, context):
        models_used.append(task.model_override)
        if task.model_override != "fallback-model-id":
            return quota_result
        return fallback_result

    with patch("oporch.smart_proxy.headroom_running", return_value=False), \
         patch("oporch.smart_proxy.call_executor_async", side_effect=fake_call), \
         patch("oporch.smart_proxy._resolve_fallback", return_value="fallback-model-id"), \
         patch("oporch.config.resolve_model", return_value="primary-model-id"):
        executor = RetryingOpenCodeExecutor(mock_base, check_headroom=False)
        result = await executor.run_async("builder", _make_task(), _make_ctx())
        assert result.success
        assert "fallback-model-id" in models_used


@pytest.mark.asyncio
async def test_non_retryable_error_returns_immediately():
    """Non-retryable errors (not rate limit or quota) return on first failure."""
    mock_base = MagicMock()
    error_result = AgentResult(success=False, error="syntax error in code")

    call_count = 0

    async def fake_call(executor, role, task, context):
        nonlocal call_count
        call_count += 1
        return error_result

    with patch("oporch.smart_proxy.headroom_running", return_value=False), \
         patch("oporch.smart_proxy.call_executor_async", side_effect=fake_call), \
         patch("oporch.config.resolve_model", return_value="test-model"):
        executor = RetryingOpenCodeExecutor(mock_base, check_headroom=False, max_retries=3)
        result = await executor.run_async("builder", _make_task(), _make_ctx())
        assert not result.success
        assert call_count == 1  # no retry for non-retryable error
