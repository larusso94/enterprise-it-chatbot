"""
Enterprise IT Chatbot - Resilience Utilities

This module provides retry mechanisms, circuit breakers, and fallback strategies
for improving the reliability of external service calls.
"""

import asyncio
import time
from functools import wraps
from typing import Callable, Any, Optional, Dict, List, Union
from dataclasses import dataclass
from enum import Enum
import logging

# Local imports
from ..mcp.config import AppConfig
from .logging_client import log


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"        # Normal operation
    OPEN = "open"           # Circuit is open, calls fail fast
    HALF_OPEN = "half_open"  # Testing if service is recovered


@dataclass
class RetryConfig:
    """Configuration for retry mechanisms."""
    max_attempts: int
    base_delay: float
    max_delay: float
    backoff_factor: float
    
    @classmethod
    def from_config(cls, config: AppConfig) -> 'RetryConfig':
        """Create RetryConfig from main AppConfig object."""
        return cls(
            max_attempts=config.max_retry_attempts,
            base_delay=config.retry_base_delay,
            max_delay=config.retry_max_delay,
            backoff_factor=config.retry_backoff_factor
        )


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int
    recovery_timeout: int
    expected_exception_threshold: int
    
    @classmethod
    def from_config(cls, config: AppConfig) -> 'CircuitBreakerConfig':
        """Create CircuitBreakerConfig from main AppConfig object."""
        return cls(
            failure_threshold=config.circuit_breaker_failure_threshold,
            recovery_timeout=config.circuit_breaker_recovery_timeout,
            expected_exception_threshold=config.circuit_breaker_expected_exception_threshold
        )


class CircuitBreaker:
    """Circuit breaker pattern implementation."""
    
    def __init__(self, config: CircuitBreakerConfig, service_name: str = "unknown"):
        self.config = config
        self.service_name = service_name
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.success_count = 0
        
    def can_execute(self) -> bool:
        """Check if execution is allowed based on circuit state."""
        now = time.time()
        
        if self.state == CircuitState.CLOSED:
            return True
            
        elif self.state == CircuitState.OPEN:
            if (self.last_failure_time and 
                now - self.last_failure_time >= self.config.recovery_timeout):
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                log.info("circuit_breaker.state_change", 
                        service=self.service_name,
                        state="half_open",
                        recovery_attempt=True)
                return True
            return False
            
        elif self.state == CircuitState.HALF_OPEN:
            return True
            
        return False
    
    def record_success(self):
        """Record a successful execution."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= 2:  # Need 2 successes to close
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                log.info("circuit_breaker.state_change",
                        service=self.service_name,
                        state="closed",
                        recovery_successful=True)
        elif self.state == CircuitState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)  # Reduce failure count on success
            
    def record_failure(self, exception: Exception):
        """Record a failed execution."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                log.warning("circuit_breaker.state_change",
                           service=self.service_name,
                           state="open",
                           failure_count=self.failure_count,
                           exception_type=type(exception).__name__)
                           
        elif self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            log.warning("circuit_breaker.state_change",
                       service=self.service_name,
                       state="open",
                       half_open_failed=True)


class ResilienceManager:
    """Manages retry logic and circuit breakers for various services."""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.retry_config = RetryConfig.from_config(config)
        self.circuit_config = CircuitBreakerConfig.from_config(config)
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        
    def get_circuit_breaker(self, service_name: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a service."""
        if service_name not in self.circuit_breakers:
            self.circuit_breakers[service_name] = CircuitBreaker(
                self.circuit_config, service_name
            )
        return self.circuit_breakers[service_name]
    
    async def execute_with_retry(self, 
                               func: Callable,
                               service_name: str,
                               *args,
                               fallback_response: Optional[Any] = None,
                               **kwargs) -> Any:
        """Execute a function with retry logic and circuit breaker."""
        circuit_breaker = self.get_circuit_breaker(service_name)
        
        # Check circuit breaker
        if not circuit_breaker.can_execute():
            log.warning("resilience.circuit_open",
                       service=service_name,
                       fallback_used=fallback_response is not None)
            if fallback_response is not None:
                return fallback_response
            raise Exception(f"Circuit breaker is OPEN for service: {service_name}")
        
        last_exception = None
        
        for attempt in range(1, self.retry_config.max_attempts + 1):
            try:
                # Execute the function
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                # Record success
                circuit_breaker.record_success()
                
                if attempt > 1:
                    log.info("resilience.retry_success",
                           service=service_name,
                           attempt=attempt,
                           total_attempts=self.retry_config.max_attempts)
                
                return result
                
            except Exception as e:
                last_exception = e
                circuit_breaker.record_failure(e)
                
                log.warning("resilience.attempt_failed",
                           service=service_name,
                           attempt=attempt,
                           total_attempts=self.retry_config.max_attempts,
                           exception_type=type(e).__name__,
                           exception_msg=str(e))
                
                # Don't retry on last attempt
                if attempt >= self.retry_config.max_attempts:
                    break
                
                # Calculate backoff delay
                delay = min(
                    self.retry_config.base_delay * (self.retry_config.backoff_factor ** (attempt - 1)),
                    self.retry_config.max_delay
                )
                
                log.info("resilience.backoff_delay",
                        service=service_name,
                        delay_seconds=delay,
                        next_attempt=attempt + 1)
                
                await asyncio.sleep(delay)
        
        # All retries failed
        log.error("resilience.all_retries_failed",
                 service=service_name,
                 total_attempts=self.retry_config.max_attempts,
                 final_exception=str(last_exception))
        
        # Use fallback if available
        if fallback_response is not None:
            log.info("resilience.fallback_used",
                    service=service_name,
                    exception_type=type(last_exception).__name__)
            return fallback_response
            
        # Re-raise the last exception
        raise last_exception


def with_resilience(service_name: str, 
                   fallback_response: Optional[Any] = None,
                   config: Optional[AppConfig] = None):
    """Decorator to add resilience to functions."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not config:
                # Try to get config from self if it's a method
                if args and hasattr(args[0], 'config'):
                    resilience_config = args[0].config
                elif args and hasattr(args[0], '_config'):
                    resilience_config = args[0]._config
                else:
                    # Import the global config instance instead of trying to create a new Config
                    from ..mcp.config import config as global_config
                    resilience_config = global_config
            else:
                resilience_config = config
                
            manager = ResilienceManager(resilience_config)
            return await manager.execute_with_retry(
                func, service_name, *args, 
                fallback_response=fallback_response, 
                **kwargs
            )
            
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # For sync functions, create an async wrapper
            async def async_version():
                return func(*args, **kwargs)
            
            return asyncio.run(async_wrapper(*args, **kwargs))
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
            
    return decorator