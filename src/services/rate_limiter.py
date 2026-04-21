"""Rate limiting and retry logic for LLM API calls.

This module provides:
1. Tenacity-based exponential backoff with 60s max wait for 429 errors
2. aiolimiter-based hard rate limiting (configurable RPM)
3. Circuit breaker pattern for fallback to Claude
4. Fire-and-forget async pattern for non-blocking add_memory calls
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeVar

from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_delay,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = 'closed'  # Normal operation
    OPEN = 'open'  # Failing, reject requests
    HALF_OPEN = 'half_open'  # Testing if service recovered


@dataclass
class CircuitBreaker:
    """Circuit breaker for API fallback.

    Opens after threshold failures, waits reset_timeout before trying again.
    """

    threshold: int = 3  # Failures before opening
    reset_timeout: float = 60.0  # Seconds to wait before half-open
    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = field(default=0)
    last_failure_time: float | None = field(default=None)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute function through circuit breaker."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self.last_failure_time and (time.time() - self.last_failure_time > self.reset_timeout):
                    logger.info('Circuit breaker: transitioning to HALF_OPEN')
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise CircuitOpenError(
                        f'Circuit is OPEN. Retry after {self.reset_timeout - (time.time() - (self.last_failure_time or 0)):.1f}s'
                    )

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception:
            await self._on_failure()
            raise

    async def _on_success(self):
        """Reset circuit on success."""
        async with self._lock:
            self.failure_count = 0
            if self.state == CircuitState.HALF_OPEN:
                logger.info('Circuit breaker: CLOSED (recovered)')
            self.state = CircuitState.CLOSED

    async def _on_failure(self):
        """Track failure and potentially open circuit."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.threshold:
                logger.warning(f'Circuit breaker: OPEN after {self.failure_count} failures')
                self.state = CircuitState.OPEN


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


class RateLimitError(Exception):
    """Raised when rate limit is hit."""
    pass


@dataclass
class RateLimiterConfig:
    """Configuration for rate limiting."""

    # Hard rate limit (requests per minute)
    requests_per_minute: int = 60

    # Exponential backoff settings
    max_retry_wait: float = 60.0  # Maximum wait time in seconds
    initial_wait: float = 1.0  # Initial wait time
    max_wait: float = 60.0  # Max wait between retries
    jitter: float = 1.0  # Random jitter to add

    # Circuit breaker settings
    circuit_threshold: int = 3  # Failures before opening circuit
    circuit_reset_timeout: float = 60.0  # Seconds before half-open

    # Fallback provider (e.g., 'anthropic' for Claude)
    fallback_provider: str | None = None


class RateLimitedLLMWrapper:
    """Wrapper that adds rate limiting and retry logic to any LLM client.

    Features:
    1. Hard rate limiting via aiolimiter (token bucket algorithm)
    2. Exponential backoff with jitter for 429 errors (tenacity)
    3. Circuit breaker for fallback to alternative provider
    4. Async fire-and-forget support
    """

    def __init__(
        self,
        client: Any,
        config: RateLimiterConfig | None = None,
        fallback_client: Any | None = None,
        fallback_clients: list[tuple[str, Any]] | None = None,
    ):
        self.client = client
        self.config = config or RateLimiterConfig()
        # Support both legacy single fallback and new tiered fallback chain
        self.fallback_client = fallback_client
        self.fallback_clients = fallback_clients or []
        if fallback_client and not fallback_clients:
            # Legacy compat: wrap single fallback as tier chain
            self.fallback_clients = [('fallback', fallback_client)]

        # Create rate limiter: X requests per 60 seconds
        self._rate_limiter = AsyncLimiter(
            max_rate=self.config.requests_per_minute,
            time_period=60.0
        )

        # Circuit breakers for primary and each fallback tier
        self._primary_circuit = CircuitBreaker(
            threshold=self.config.circuit_threshold,
            reset_timeout=self.config.circuit_reset_timeout,
        )
        self._fallback_circuit = CircuitBreaker(
            threshold=self.config.circuit_threshold,
            reset_timeout=self.config.circuit_reset_timeout,
        )
        self._tier_circuits: dict[str, CircuitBreaker] = {
            name: CircuitBreaker(
                threshold=self.config.circuit_threshold,
                reset_timeout=self.config.circuit_reset_timeout,
            )
            for name, _ in self.fallback_clients
        }

        # Metrics
        self._request_count = 0
        self._retry_count = 0
        self._fallback_count = 0
        self._tier_counts: dict[str, int] = {name: 0 for name, _ in self.fallback_clients}
        self._last_request_time: datetime | None = None

    @property
    def chat(self):
        """Return self to maintain client.chat.completions.create() interface."""
        return self

    @property
    def completions(self):
        """Return self to maintain client.chat.completions.create() interface."""
        return self

    async def create(self, **kwargs) -> Any:
        """Rate-limited API call with tiered fallback chain.

        Cascade: Primary → Tier 2 → Tier 3 → ... → raise last error.
        Each tier has its own circuit breaker.
        """
        self._request_count += 1
        self._last_request_time = datetime.now(timezone.utc)

        # Try primary with rate limiting and retry
        try:
            response = await self._call_with_retry_and_rate_limit(
                self.client, self._primary_circuit, 'primary', **kwargs
            )
            # Detect reasoning model empty output: content="" with tokens consumed
            self._validate_response(response, 'primary')
            return response
        except Exception as primary_error:
            logger.warning(f'Primary LLM failed: {primary_error}')

            # Cascade through fallback tiers
            if self.fallback_clients:
                last_error = primary_error
                for tier_name, tier_client in self.fallback_clients:
                    self._fallback_count += 1
                    self._tier_counts[tier_name] = self._tier_counts.get(tier_name, 0) + 1
                    circuit = self._tier_circuits.get(tier_name, self._fallback_circuit)
                    logger.info(f'Attempting fallback tier: {tier_name}')
                    try:
                        response = await self._call_with_retry_and_rate_limit(
                            tier_client, circuit, tier_name, **kwargs
                        )
                        self._validate_response(response, tier_name)
                        logger.info(f'Tier {tier_name} succeeded')
                        return response
                    except Exception as tier_error:
                        logger.warning(f'Tier {tier_name} failed: {tier_error}')
                        last_error = tier_error
                        continue  # Try next tier

                logger.error('All fallback tiers exhausted')
                raise last_error from primary_error

            raise primary_error

    def _validate_response(self, response: Any, tier_name: str) -> None:
        """Detect reasoning model failures: empty content with tokens consumed.

        Raises ValueError to trigger fallback to next tier.
        """
        try:
            if not hasattr(response, 'choices') or not response.choices:
                raise ValueError(f'[{tier_name}] Response has no choices')

            choice = response.choices[0]
            content = getattr(choice.message, 'content', None) or ''
            finish_reason = getattr(choice, 'finish_reason', None)

            # Detect empty output from reasoning model token exhaustion
            if not content.strip():
                usage = getattr(response, 'usage', None)
                reasoning_tokens = 0
                if usage:
                    details = getattr(usage, 'completion_tokens_details', None)
                    if details:
                        reasoning_tokens = getattr(details, 'reasoning_tokens', 0) or 0

                if reasoning_tokens > 0:
                    raise ValueError(
                        f'[{tier_name}] Reasoning model exhausted token budget: '
                        f'{reasoning_tokens} reasoning tokens, 0 output tokens. '
                        f'finish_reason={finish_reason}'
                    )
                elif finish_reason == 'content_filter':
                    raise ValueError(
                        f'[{tier_name}] Content filter triggered: empty output'
                    )
                elif finish_reason == 'length':
                    raise ValueError(
                        f'[{tier_name}] Output truncated (finish_reason=length): empty content'
                    )
        except (AttributeError, TypeError):
            # If response structure is unexpected, let downstream handle it
            pass

    async def _call_with_retry_and_rate_limit(
        self,
        client: Any,
        circuit: CircuitBreaker,
        provider_name: str,
        **kwargs
    ) -> Any:
        """Execute API call with rate limiting, retry, and circuit breaker."""

        async def _make_call():
            # Wait for rate limiter (hard limit)
            async with self._rate_limiter:
                logger.debug(f'[{provider_name}] Making API call (rate limited)')

                # Get the actual create method
                if hasattr(client, 'chat') and hasattr(client.chat, 'completions'):
                    response = await client.chat.completions.create(**kwargs)
                elif hasattr(client, 'create'):
                    response = await client.create(**kwargs)
                else:
                    raise ValueError(f'Client {type(client)} does not have create method')

                return response

        # Use tenacity for exponential backoff retry
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((RateLimitError, Exception)),
                wait=wait_exponential_jitter(
                    initial=self.config.initial_wait,
                    max=self.config.max_wait,
                    jitter=self.config.jitter,
                ),
                stop=stop_after_delay(self.config.max_retry_wait),
                reraise=True,
            ):
                with attempt:
                    if attempt.retry_state.attempt_number > 1:
                        self._retry_count += 1
                        elapsed = attempt.retry_state.seconds_since_start or 0.0
                        logger.info(
                            f'[{provider_name}] Retry attempt {attempt.retry_state.attempt_number} '
                            f'after {elapsed:.1f}s'
                        )

                    try:
                        return await circuit.call(_make_call)
                    except CircuitOpenError:
                        # Circuit is open, don't retry - let it propagate
                        raise
                    except Exception as e:
                        # Check if it's a rate limit error (429)
                        error_str = str(e).lower()
                        if '429' in error_str or 'rate' in error_str and 'limit' in error_str:
                            logger.warning(f'[{provider_name}] Rate limit hit, will retry with backoff')
                            raise RateLimitError(str(e)) from e
                        raise

        except RetryError as e:
            logger.error(f'[{provider_name}] Max retries exceeded after {self.config.max_retry_wait}s')
            last_exception = e.last_attempt.exception() if e.last_attempt.exception() else e
            raise last_exception from e

    def get_metrics(self) -> dict:
        """Return current metrics."""
        metrics = {
            'request_count': self._request_count,
            'retry_count': self._retry_count,
            'fallback_count': self._fallback_count,
            'last_request_time': self._last_request_time.isoformat() if self._last_request_time else None,
            'primary_circuit_state': self._primary_circuit.state.value,
            'fallback_circuit_state': self._fallback_circuit.state.value if self.fallback_client else None,
        }
        # Add per-tier metrics
        for tier_name, _ in self.fallback_clients:
            circuit = self._tier_circuits.get(tier_name)
            metrics[f'tier_{tier_name}_count'] = self._tier_counts.get(tier_name, 0)
            metrics[f'tier_{tier_name}_circuit'] = circuit.state.value if circuit else None
        return metrics


# Global rate limiter instance for the MCP server
_global_rate_limiter: RateLimitedLLMWrapper | None = None


def get_global_rate_limiter() -> RateLimitedLLMWrapper | None:
    """Get the global rate limiter instance."""
    return _global_rate_limiter


def set_global_rate_limiter(limiter: RateLimitedLLMWrapper):
    """Set the global rate limiter instance."""
    global _global_rate_limiter
    _global_rate_limiter = limiter


# Fire-and-forget async task management
@dataclass
class AsyncTaskStatus:
    """Status of an async fire-and-forget task."""

    task_id: str
    name: str
    status: str  # 'pending', 'running', 'completed', 'failed'
    created_at: datetime
    completed_at: datetime | None = None
    result: Any | None = None
    error: str | None = None


class AsyncTaskManager:
    """Manager for fire-and-forget async tasks.

    Allows add_memory to return immediately while processing continues in background.
    """

    def __init__(self, max_tasks: int = 100):
        self._tasks: dict[str, AsyncTaskStatus] = {}
        self._max_tasks = max_tasks
        self._lock = asyncio.Lock()

    async def submit(
        self,
        task_id: str,
        name: str,
        coro: Any,
        callback: Callable[[AsyncTaskStatus], None] | None = None,
    ) -> str:
        """Submit a task for async execution.

        Returns immediately with task_id. Task runs in background.
        """
        async with self._lock:
            # Clean up old completed tasks if at max
            if len(self._tasks) >= self._max_tasks:
                completed = [
                    tid for tid, t in self._tasks.items()
                    if t.status in ('completed', 'failed')
                ]
                for tid in completed[:len(completed) // 2]:  # Remove half
                    del self._tasks[tid]

            status = AsyncTaskStatus(
                task_id=task_id,
                name=name,
                status='pending',
                created_at=datetime.now(timezone.utc),
            )
            self._tasks[task_id] = status

        # Create background task
        asyncio.create_task(self._run_task(task_id, coro, callback))
        return task_id

    async def _run_task(
        self,
        task_id: str,
        coro: Any,
        callback: Callable[[AsyncTaskStatus], None] | None,
    ):
        """Run task and update status."""
        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = 'running'

        try:
            result = await coro
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = 'completed'
                    self._tasks[task_id].completed_at = datetime.now(timezone.utc)
                    self._tasks[task_id].result = result
                    logger.info(f'Task {task_id} completed successfully')

            if callback:
                callback(self._tasks.get(task_id))

        except Exception as e:
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = 'failed'
                    self._tasks[task_id].completed_at = datetime.now(timezone.utc)
                    self._tasks[task_id].error = str(e)
                    logger.error(f'Task {task_id} failed: {e}')

            if callback:
                callback(self._tasks.get(task_id))

    async def get_status(self, task_id: str) -> AsyncTaskStatus | None:
        """Get status of a task."""
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(self, status_filter: str | None = None) -> list[AsyncTaskStatus]:
        """List all tasks, optionally filtered by status."""
        async with self._lock:
            tasks = list(self._tasks.values())
            if status_filter:
                tasks = [t for t in tasks if t.status == status_filter]
            return tasks


# Global task manager instance
_task_manager: AsyncTaskManager | None = None


def get_task_manager() -> AsyncTaskManager:
    """Get or create the global task manager."""
    global _task_manager
    if _task_manager is None:
        _task_manager = AsyncTaskManager()
    return _task_manager
