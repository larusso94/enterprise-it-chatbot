"""
Structured logging client for CU1 IT Chatbot.

This module provides centralized logging configuration optimized for Azure Container Apps
and development environments. It uses structlog for structured logging with JSON output
for production and human-readable format for development.

Features:
- Configurable JSON or console output
- Bot Framework noise filtering
- Azure Container Apps optimization
- Structured data logging
- Multiple log level support
- Uvicorn integration
"""

import sys
import logging
import structlog
from ..mcp.config import config


def setup_logger():
    """Configure and return a structlog logger optimized for Azure Container Apps.

    Environment variables:
      log-level: base level (default INFO)
      log-json: if '0' disables JSON output (human console rendering)
    """
    log_level = config.log_level
    use_json = config.log_json

    # Configure Python's root logger for Azure Container Apps
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s" if not use_json else "%(message)s",
        stream=sys.stdout,
        force=True  # Override any existing configuration
    )
    
    # Filter Bot Framework warnings about unknown Teams attributes
    class BotFrameworkFilter(logging.Filter):
        def filter(self, record):
            message = record.getMessage()
            # Suppress specific Bot Framework warnings about Teams attributes
            suppress_patterns = [
                "is not a known attribute of class",
                "TeamsChannelData",
                "and will be ignored"
            ]
            if all(pattern in message for pattern in suppress_patterns):
                return False
            return True
    
    # Apply filter to root logger and handlers
    bot_filter = BotFrameworkFilter()
    root_logger = logging.getLogger()
    root_logger.addFilter(bot_filter)
    
    # Ensure all loggers output to stdout for Container Apps
    if root_logger.handlers:
        for handler in root_logger.handlers:
            handler.setStream(sys.stdout)
            handler.addFilter(bot_filter)
    
    # Configure structlog processors
    timestamper = structlog.processors.TimeStamper(fmt="iso")
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
    ]
    
    if use_json:
        # JSON format for production/Azure
        processors.extend([
            structlog.processors.JSONRenderer(sort_keys=True)
        ])
    else:
        # Human-readable format for development
        processors.extend([
            structlog.dev.ConsoleRenderer(colors=False)  # No colors in containers
        ])

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.WriteLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    logger = structlog.get_logger()
    
    # Test logging to ensure it works
    logger.info("logging.initialized", 
                level=log_level, 
                json_format=use_json,
                handlers=len(root_logger.handlers))
    
    return logger


def get_logger():
    """Get the configured logger, ensuring it's set up properly for containers."""
    return setup_logger()

def test_logging():
    """Test function to verify logging is working correctly."""
    logger = get_logger()
    
    # Test different log levels
    logger.debug("logging.test.debug", message="Debug level test")
    logger.info("logging.test.info", message="Info level test") 
    logger.warning("logging.test.warning", message="Warning level test")
    logger.error("logging.test.error", message="Error level test")
    
    # Test with structured data
    logger.info("logging.test.structured", 
                user_id="test-user",
                session_id="test-session", 
                count=42,
                active=True)
    
    return True

# Initialize logger
log = setup_logger()

# Ensure uvicorn logs go to stdout as well
def configure_uvicorn_logging():
    """Configure uvicorn logging to use our setup."""
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    
    # Remove any existing handlers and use our configuration
    for logger in [uvicorn_logger, uvicorn_access_logger]:
        logger.handlers = []
        logger.addHandler(logging.StreamHandler(sys.stdout))
        logger.setLevel(getattr(logging, config.log_level, logging.INFO))

# Configure uvicorn when module is imported
configure_uvicorn_logging()
