"""Retry wrapper for httpx with tenacity."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_exception_message,
)

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for HTTP retry behavior."""
    max_retries: int = 3
    backoff_factor: float = 0.5
    retryable_status_codes: list[int] = field(default_factory=lambda: [500, 502, 503, 504])
    retryable_exceptions: tuple[type[Exception], ...] = field(default_factory=lambda: (
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.TimeoutException,
    ))


def _should_retry_status(response: httpx.Response) -> bool:
    """Check if status code should trigger a retry."""
    return response.status_code in RETRY_CONFIG.retryable_status_codes


# Default retry config instance
RETRY_CONFIG = RetryConfig()


@retry(
    stop=stop_after_attempt(lambda state: state.attempt_number <= RETRY_CONFIG.max_retries),
    wait=wait_exponential(multiplier=RETRY_CONFIG.backoff_factor, min=1, max=30),
    retry=(
        retry_if_exception_type(RETRY_CONFIG.retryable_exceptions) |
        retry_if_exception_message(
            match=r"(connection|timeout|read|write|connect)",
            reraise=False
        )
    ),
    reraise=True,
    before_sleep=lambda retry_state: logger.warning(
        "Retry attempt %d/%d: %s", retry_state.attempt_number, retry_state.attempt_total, retry_state.outcome.exception()
    ) if retry_state.outcome.failed else None
)
def _retry_request(func, *args, **kwargs):
    """Internal retry wrapper for httpx requests."""
    return func(*args, **kwargs)


class RetryClient:
    """
    An httpx Client wrapper that automatically retries on transient failures.
    
    Example:
        ```python
        client = RetryClient()
        response = client.get("https://api.example.com")
        client.close()
        ```
    
    Or as a context manager:
        ```python
        with RetryClient() as client:
            response = client.get("https://api.example.com")
        ```
    """
    
    def __init__(self, config: RetryConfig | None = None, **kwargs):
        """
        Initialize the retry client.
        
        Args:
            config: Optional RetryConfig to override defaults.
            **kwargs: Additional arguments passed to httpx.Client.
        """
        self._config = config or RETRY_CONFIG
        self._client = httpx.Client(**kwargs)
    
    def __enter__(self) -> "RetryClient":
        """Enter context manager."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager and close the client."""
        self.close()
    
    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()
    
    def get(self, url: str, **kwargs) -> httpx.Response:
        """Make a GET request with retry logic."""
        return _retry_request(self._client.get, url, **kwargs)
    
    def post(self, url: str, **kwargs) -> httpx.Response:
        """Make a POST request with retry logic."""
        return _retry_request(self._client.post, url, **kwargs)
    
    def put(self, url: str, **kwargs) -> httpx.Response:
        """Make a PUT request with retry logic."""
        return _retry_request(self._client.put, url, **kwargs)
    
    def patch(self, url: str, **kwargs) -> httpx.Response:
        """Make a PATCH request with retry logic."""
        return _retry_request(self._client.patch, url, **kwargs)
    
    def delete(self, url: str, **kwargs) -> httpx.Response:
        """Make a DELETE request with retry logic."""
        return _retry_request(self._client.delete, url, **kwargs)
    
    def head(self, url: str, **kwargs) -> httpx.Response:
        """Make a HEAD request with retry logic."""
        return _retry_request(self._client.head, url, **kwargs)
    
    def options(self, url: str, **kwargs) -> httpx.Response:
        """Make an OPTIONS request with retry logic."""
        return _retry_request(self._client.options, url, **kwargs)


def retry_httpx(func):
    """
    Decorator to add retry logic to any function that makes httpx requests.
    
    Example:
        ```python
        @retry_httpx
        def fetch_data(url):
            with httpx.Client() as client:
                return client.get(url)
        ```
    """
    return _retry_request(func)


def get_retry_client(**kwargs) -> RetryClient:
    """
    Convenience function to create a RetryClient with default configuration.
    
    Args:
        **kwargs: Additional arguments passed to httpx.Client.
    
    Returns:
        A configured RetryClient instance.
    """
    return RetryClient(**kwargs)
