"""
Retry handler with exponential backoff for transient failures.
Prevents pipeline/workflow failures due to temporary API issues.
"""
import contextlib
import contextvars
import logging
from functools import wraps

import requests
from anthropic import APIError as AnthropicError
from anthropic import RateLimitError as AnthropicRateLimitError
from openai import APIError as OpenAIError
from openai import RateLimitError as OpenAIRateLimitError
from tenacity import (
    after_log,
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.stop import stop_base

logger = logging.getLogger(__name__)


# Define transient errors that should be retried
TRANSIENT_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    OpenAIError,
    OpenAIRateLimitError,
    # Anthropic errors - but we'll handle credit/balance errors specially
    AnthropicError,
    AnthropicRateLimitError,
)


_one_attempt = contextvars.ContextVar("retry_one_attempt", default=False)


@contextlib.contextmanager
def one_attempt():
    """Inside this block every call decorated with ``retry_on_transient_error`` is tried once.

    A caller that has a time limit uses it so the retries do not multiply the limit."""
    token = _one_attempt.set(True)
    try:
        yield
    finally:
        _one_attempt.reset(token)


class _stop_when_one_attempt(stop_base):
    def __call__(self, retry_state):
        return _one_attempt.get()


def retry_on_transient_error(max_attempts=3, min_wait=2, max_wait=10):
    """
    Decorator for retrying functions on transient errors.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        min_wait: Minimum wait time in seconds (default: 2)
        max_wait: Maximum wait time in seconds (default: 10)

    Usage:
        @retry_on_transient_error(max_attempts=5)
        def call_llm_api():
            # your code here
    """

    def should_retry(retry_state):
        """Custom retry predicate that excludes credit/balance errors."""
        exception = retry_state.outcome.exception()
        if exception is None:
            return False

        error_str = str(exception).lower()

        # Don't retry credit/balance/quota errors - these won't resolve within the retry window
        if any(
            phrase in error_str
            for phrase in [
                "credit balance", "insufficient credits", "quota exceeded",
                "billing", "usage limit", "api usage limit",
            ]
        ):
            return False

        # Don't retry authentication errors
        if any(
            phrase in error_str for phrase in ["authentication", "invalid api key", "unauthorized"]
        ):
            return False

        # Retry other transient errors
        return isinstance(exception, TRANSIENT_ERRORS)

    return retry(
        stop=stop_after_attempt(max_attempts) | _stop_when_one_attempt(),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=should_retry,
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO),
        reraise=True,
    )


class RetryHandler:
    """
    Retry handler for manual retry logic.
    Use when you need more control than the decorator provides.
    """

    def __init__(self, max_attempts=3, backoff_base=2):
        """
        Initialize retry handler.

        Args:
            max_attempts: Maximum number of attempts
            backoff_base: Base for exponential backoff (default: 2)
        """
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base

    def execute_with_retry(self, func, *args, **kwargs):
        """
        Execute function with retry logic.

        Returns:
            tuple: (success: bool, result: Any, attempts: int, error: Exception|None)

        Example:
            handler = RetryHandler()
            success, result, attempts, error = handler.execute_with_retry(
                llm_service.call, prompt="Generate code"
            )
            if success:
                logger.info(f"Succeeded on attempt {attempts}")
            else:
                logger.info(f"Failed after {attempts} attempts: {error}")
        """
        last_error = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                logger.info(f"Attempt {attempt}/{self.max_attempts}: {func.__name__}")
                result = func(*args, **kwargs)
                logger.info(f"Success on attempt {attempt}")
                return True, result, attempt, None

            except TRANSIENT_ERRORS as e:
                last_error = e

                if attempt == self.max_attempts:
                    logger.error(f"Failed after {attempt} attempts: {e}")
                    return False, None, attempt, e

                wait_time = self.backoff_base**attempt
                logger.warning(
                    f"Attempt {attempt} failed ({type(e).__name__}), "
                    f"retrying in {wait_time}s: {str(e)[:100]}"
                )

                import time

                time.sleep(wait_time)

            except Exception as e:
                # Non-retryable error - fail immediately
                logger.error(f"Non-retryable error: {type(e).__name__}: {e}")
                return False, None, attempt, e

        return False, None, self.max_attempts, last_error

    def is_retryable(self, error):
        """Check if an error is retryable."""
        return isinstance(error, TRANSIENT_ERRORS)


class CircuitBreaker:
    """
    Circuit breaker pattern to prevent cascading failures.
    Opens circuit when failure threshold is reached, preventing further calls.
    """

    def __init__(self, failure_threshold=5, timeout_seconds=60):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            timeout_seconds: Seconds before attempting to close circuit
        """
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.state = "closed"  # closed, open, half_open
        self.last_failure_time = None

    def call(self, func, *args, **kwargs):
        """
        Execute function through circuit breaker.

        Raises:
            CircuitBreakerOpenError: If circuit is open

        Returns:
            Result of function call
        """
        # Check if we should attempt to close the circuit
        if self.state == "open":
            import time

            if time.time() - self.last_failure_time > self.timeout_seconds:
                self.state = "half_open"
                logger.info("Circuit breaker entering half-open state")
            else:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker is open. Last failure: {self.last_failure_time}"
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self):
        """Handle successful call."""
        if self.state == "half_open":
            logger.info("Circuit breaker closing after successful call")
        self.failure_count = 0
        self.state = "closed"

    def _on_failure(self):
        """Handle failed call."""
        import time

        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.error(
                f"Circuit breaker opened after {self.failure_count} failures. "
                f"Will retry after {self.timeout_seconds}s"
            )

    def reset(self):
        """Manually reset circuit breaker."""
        self.failure_count = 0
        self.state = "closed"
        self.last_failure_time = None
        logger.info("Circuit breaker manually reset")


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""

    pass


def db_transaction_retry(max_retries: int = 3, base_delay: float = 0.1):
    """
    Execute a database operation with retry logic for CockroachDB transaction errors.

    Handles SerializationFailure and TransactionRetry errors with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Base delay in seconds for exponential backoff (default: 0.1)

    Returns:
        Decorator function

    Usage:
        @db_transaction_retry(max_retries=3)
        def save_record():
            db.session.add(record)
            db.session.commit()
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time

            from sqlalchemy.exc import OperationalError

            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    error_str = str(e)
                    if "SerializationFailure" in error_str or "TransactionRetry" in error_str:
                        last_error = e
                        if attempt < max_retries - 1:
                            # Import db here to avoid circular import
                            from app import db

                            db.session.rollback()
                            delay = base_delay * (attempt + 1)
                            time.sleep(delay)
                            logger.warning(
                                f"Transaction retry {func.__name__}: attempt {attempt + 1}/{max_retries}"
                            )
                            continue
                        else:
                            logger.error(f"Max retries reached for {func.__name__}: {e}")
                            raise
                    else:
                        raise

            if last_error:
                raise last_error

        return wrapper

    return decorator


def execute_with_db_retry(
    operation,
    max_retries: int = 3,
    base_delay: float = 0.1,
    operation_name: str = "database operation",
):
    """
    Execute a database operation with retry logic for CockroachDB transaction errors.

    Use this function when you can't use the decorator (e.g., inline operations).

    Args:
        operation: Callable to execute
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Base delay in seconds for exponential backoff (default: 0.1)
        operation_name: Name for logging purposes

    Returns:
        tuple: (success: bool, result: Any, error: Exception|None)

    Usage:
        def update_record():
            record.status = "completed"
            db.session.commit()

        success, result, error = execute_with_db_retry(update_record, operation_name="update status")
    """
    import time

    from sqlalchemy.exc import OperationalError

    last_error = None
    for attempt in range(max_retries):
        try:
            result = operation()
            return True, result, None
        except OperationalError as e:
            error_str = str(e)
            if "SerializationFailure" in error_str or "TransactionRetry" in error_str:
                last_error = e
                if attempt < max_retries - 1:
                    from app import db

                    db.session.rollback()
                    delay = base_delay * (attempt + 1)
                    time.sleep(delay)
                    logger.warning(
                        f"Transaction retry {operation_name}: attempt {attempt + 1}/{max_retries}"
                    )
                    continue
                else:
                    logger.error(f"Max retries reached for {operation_name}: {e}")
                    return False, None, e
            else:
                return False, None, e

    return False, None, last_error
