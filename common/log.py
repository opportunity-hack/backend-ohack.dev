import os
import logging
import json
import traceback
from datetime import datetime
import sys
from typing import Any, Dict, Optional, Union

# Configure default log level
log_level = logging.INFO

if 'GLOBAL_LOG_LEVEL' in os.environ:
    level_map = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warning': logging.WARNING,
        'error': logging.ERROR,
        'critical': logging.CRITICAL
    }
    log_level = level_map.get(os.environ['GLOBAL_LOG_LEVEL'].lower(), logging.INFO)

# Configure JSON formatter for structured logging
class JsonFormatter(logging.Formatter):
    """Custom formatter that outputs logs as JSON objects for better parsing"""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a JSON object"""
        log_entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }
        
        # Add exception info if available
        if record.exc_info:
            log_entry['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': traceback.format_exception(*record.exc_info)
            }
        
        # Extract extra attributes from the record
        # Skip standard LogRecord attributes to get only the custom ones
        standard_attrs = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
            'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
            'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
            'processName', 'process', 'getMessage', 'extra'
        }
        
        extra_data = {}
        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                extra_data[key] = value
        
        if extra_data:
            log_entry['extra'] = extra_data
            
        return json.dumps(log_entry)

# Determine if we should use JSON logging based on environment
use_json_logging = os.environ.get('USE_JSON_LOGGING', 'false').lower() == 'true'

# Root logger configuration
def configure_root_logger():
    """Configure the root logger with appropriate handlers"""
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove existing handlers if any
    for handler in root_logger.handlers[:]: 
        root_logger.removeHandler(handler)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    # Apply appropriate formatter
    if use_json_logging:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

# Initialize root logger
configure_root_logger()

def get_log_level() -> int:
    """Get the current log level"""
    return log_level

def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name"""
    logger = logging.getLogger(name)
    logger.setLevel(get_log_level())
    return logger

# Extended logging function for structured logs
def log_structured(logger: logging.Logger, level: int, message: str, **kwargs) -> None:
    """Log a message with structured data
    
    Args:
        logger: The logger instance to use
        level: The log level (e.g., logging.INFO)
        message: The log message
        **kwargs: Additional structured data to include in the log
    """
    if not logger.isEnabledFor(level):
        return
    
    # Extract exc_info if present in kwargs
    exc_info = kwargs.pop('exc_info', None)
    
    # Filter out kwargs that would conflict with standard LogRecord attributes
    standard_attrs = {
        'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
        'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
        'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
        'processName', 'process', 'getMessage', 'extra'
    }
    
    safe_kwargs = {k: v for k, v in kwargs.items() if k not in standard_attrs}
    
    # For non-JSON logging, include structured data in the message
    if not use_json_logging and safe_kwargs:
        extra_info = ', '.join(f"{k}={v}" for k, v in safe_kwargs.items())
        message = f"{message} [{extra_info}]"
    
    # Pass safe_kwargs as extra - they will become attributes on the LogRecord
    logger.log(level, message, extra=safe_kwargs, exc_info=exc_info)

# Define helpers for structured logging
def debug(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log a DEBUG level message with structured data"""
    log_structured(logger, logging.DEBUG, message, **kwargs)

def info(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log an INFO level message with structured data"""
    log_structured(logger, logging.INFO, message, **kwargs)

def warning(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log a WARNING level message with structured data"""
    log_structured(logger, logging.WARNING, message, **kwargs)

def error(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log an ERROR level message with structured data"""
    log_structured(logger, logging.ERROR, message, **kwargs)

def critical(logger: logging.Logger, message: str, **kwargs) -> None:
    """Log a CRITICAL level message with structured data"""
    log_structured(logger, logging.CRITICAL, message, **kwargs)

def exception(logger: logging.Logger, message: str, exc_info: Optional[Exception] = None, **kwargs) -> None:
    """Log an exception with structured data"""
    log_structured(logger, logging.ERROR, message, exception=exc_info, **kwargs)