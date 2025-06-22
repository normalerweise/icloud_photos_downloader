#!/usr/bin/env python
"""Comprehensive error handling and logging service for production use."""

from __future__ import annotations

import logging
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Optional, TypeVar, Union

from .models import Photo
from .types import AlbumName, DataPath, LogLevel, PhotoCount


class ErrorSeverity(Enum):
    """Severity levels for errors."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Categories of errors for better organization."""
    NETWORK = "network"
    AUTHENTICATION = "authentication"
    FILE_SYSTEM = "file_system"
    ICLOUD_API = "icloud_api"
    VALIDATION = "validation"
    PERMISSION = "permission"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorDetails:
    """Immutable detailed error information."""
    
    error_id: str
    message: str
    severity: ErrorSeverity
    category: ErrorCategory
    timestamp: datetime
    photo_context: Optional[Photo] = None
    album_context: Optional[AlbumName] = None
    file_path_context: Optional[DataPath] = None
    stack_trace: Optional[str] = None
    recovery_suggestion: Optional[str] = None
    
    @classmethod
    def from_exception(
        cls, 
        error_id: str, 
        exception: Exception, 
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        photo: Optional[Photo] = None,
        album: Optional[AlbumName] = None,
        file_path: Optional[DataPath] = None,
        recovery_suggestion: Optional[str] = None
    ) -> ErrorDetails:
        """Create error details from an exception."""
        return cls(
            error_id=error_id,
            message=str(exception),
            severity=severity,
            category=category,
            timestamp=datetime.now(),
            photo_context=photo,
            album_context=album,
            file_path_context=file_path,
            stack_trace=traceback.format_exc(),
            recovery_suggestion=recovery_suggestion,
        )
    
    @property
    def is_recoverable(self) -> bool:
        """Check if error is likely recoverable."""
        return self.severity in [ErrorSeverity.INFO, ErrorSeverity.WARNING]
    
    @property
    def should_retry(self) -> bool:
        """Check if operation should be retried."""
        return (self.category in [ErrorCategory.NETWORK, ErrorCategory.ICLOUD_API] and 
                self.severity != ErrorSeverity.CRITICAL)
    
    @property
    def context_summary(self) -> str:
        """Get human-readable context summary."""
        contexts = []
        if self.photo_context:
            contexts.append(f"photo: {self.photo_context.filename}")
        if self.album_context:
            contexts.append(f"album: {self.album_context}")
        if self.file_path_context:
            contexts.append(f"path: {self.file_path_context}")
        
        return " | ".join(contexts) if contexts else "no context"


@dataclass(frozen=True)
class ErrorSummary:
    """Immutable summary of errors encountered during operations."""
    
    total_errors: int
    errors_by_severity: Dict[ErrorSeverity, int]
    errors_by_category: Dict[ErrorCategory, int]
    recoverable_errors: int
    critical_errors: int
    error_details: FrozenSet[ErrorDetails]
    
    @classmethod
    def from_errors(cls, errors: FrozenSet[ErrorDetails]) -> ErrorSummary:
        """Create error summary from error details."""
        severity_counts = {}
        category_counts = {}
        
        for error in errors:
            severity_counts[error.severity] = severity_counts.get(error.severity, 0) + 1
            category_counts[error.category] = category_counts.get(error.category, 0) + 1
        
        recoverable = sum(1 for e in errors if e.is_recoverable)
        critical = sum(1 for e in errors if e.severity == ErrorSeverity.CRITICAL)
        
        return cls(
            total_errors=len(errors),
            errors_by_severity=severity_counts,
            errors_by_category=category_counts,
            recoverable_errors=recoverable,
            critical_errors=critical,
            error_details=errors,
        )
    
    @property
    def has_critical_errors(self) -> bool:
        """Check if any critical errors occurred."""
        return self.critical_errors > 0
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate (lower is worse)."""
        if self.total_errors == 0:
            return 100.0
        return max(0.0, 100.0 - (self.critical_errors / self.total_errors * 100.0))


T = TypeVar('T')


class ProductionLogger:
    """Production-ready logger with structured logging and error tracking."""
    
    def __init__(self, name: str = "icloudpd", level: LogLevel = "info") -> None:
        self.logger = logging.getLogger(name)
        self.errors: list[ErrorDetails] = []
        self._setup_logging(level)
    
    def _setup_logging(self, level: LogLevel) -> None:
        """Set up structured logging for production use."""
        # Clear existing handlers
        self.logger.handlers.clear()
        
        # Set level
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }
        self.logger.setLevel(level_map.get(level, logging.INFO))
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # File handler (optional - can be added later)
        # file_handler = logging.FileHandler('icloudpd.log')
        # file_handler.setFormatter(formatter)
        # self.logger.addHandler(file_handler)
    
    def info(self, message: str, **context) -> None:
        """Log info message with context."""
        self.logger.info(self._format_message(message, **context))
    
    def warning(self, message: str, **context) -> None:
        """Log warning message with context."""
        self.logger.warning(self._format_message(message, **context))
    
    def error(self, message: str, **context) -> None:
        """Log error message with context."""
        self.logger.error(self._format_message(message, **context))
    
    def critical(self, message: str, **context) -> None:
        """Log critical message with context."""
        self.logger.critical(self._format_message(message, **context))
    
    def _format_message(self, message: str, **context) -> str:
        """Format message with context information."""
        if not context:
            return message
        
        context_str = " | ".join(f"{k}={v}" for k, v in context.items())
        return f"{message} | {context_str}"
    
    def log_error_details(self, error: ErrorDetails) -> None:
        """Log detailed error information."""
        self.errors.append(error)
        
        # Log at appropriate level
        log_method = {
            ErrorSeverity.INFO: self.info,
            ErrorSeverity.WARNING: self.warning,
            ErrorSeverity.ERROR: self.error,
            ErrorSeverity.CRITICAL: self.critical,
        }.get(error.severity, self.error)
        
        log_method(
            f"[{error.error_id}] {error.message}",
            category=error.category.value,
            context=error.context_summary,
            recovery=error.recovery_suggestion or "none"
        )
        
        # Log stack trace for errors and above
        if error.severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL] and error.stack_trace:
            self.logger.debug(f"Stack trace for {error.error_id}:\n{error.stack_trace}")
    
    def get_error_summary(self) -> ErrorSummary:
        """Get summary of all logged errors."""
        return ErrorSummary.from_errors(frozenset(self.errors))
    
    def clear_errors(self) -> None:
        """Clear accumulated errors."""
        self.errors.clear()


class SafeOperationWrapper:
    """Wrapper for safe execution of operations with comprehensive error handling."""
    
    def __init__(self, logger: ProductionLogger) -> None:
        self.logger = logger
    
    def safe_execute(
        self, 
        operation: Callable[[], T], 
        error_id: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        photo_context: Optional[Photo] = None,
        album_context: Optional[AlbumName] = None,
        file_path_context: Optional[DataPath] = None,
        recovery_suggestion: Optional[str] = None,
        default_return: Optional[T] = None
    ) -> Union[T, None]:
        """Safely execute an operation with comprehensive error handling."""
        try:
            return operation()
            
        except KeyboardInterrupt:
            # User interruption should be handled specially
            error = ErrorDetails(
                error_id=f"{error_id}_interrupted",
                message="Operation interrupted by user",
                severity=ErrorSeverity.WARNING,
                category=ErrorCategory.UNKNOWN,
                timestamp=datetime.now(),
                photo_context=photo_context,
                album_context=album_context,
                file_path_context=file_path_context,
                recovery_suggestion="Resume operation or use --resume flag",
            )
            self.logger.log_error_details(error)
            raise  # Re-raise keyboard interrupt
            
        except PermissionError as e:
            error = ErrorDetails.from_exception(
                error_id=f"{error_id}_permission",
                exception=e,
                severity=ErrorSeverity.ERROR,
                category=ErrorCategory.PERMISSION,
                photo=photo_context,
                album=album_context,
                file_path=file_path_context,
                recovery_suggestion=recovery_suggestion or "Check file/directory permissions",
            )
            self.logger.log_error_details(error)
            return default_return
            
        except FileNotFoundError as e:
            error = ErrorDetails.from_exception(
                error_id=f"{error_id}_file_not_found",
                exception=e,
                severity=ErrorSeverity.WARNING,
                category=ErrorCategory.FILE_SYSTEM,
                photo=photo_context,
                album=album_context,
                file_path=file_path_context,
                recovery_suggestion=recovery_suggestion or "Ensure file/directory exists",
            )
            self.logger.log_error_details(error)
            return default_return
            
        except OSError as e:
            error = ErrorDetails.from_exception(
                error_id=f"{error_id}_os_error",
                exception=e,
                severity=ErrorSeverity.ERROR,
                category=ErrorCategory.FILE_SYSTEM,
                photo=photo_context,
                album=album_context,
                file_path=file_path_context,
                recovery_suggestion=recovery_suggestion or "Check disk space and file system",
            )
            self.logger.log_error_details(error)
            return default_return
            
        except ConnectionError as e:
            error = ErrorDetails.from_exception(
                error_id=f"{error_id}_connection",
                exception=e,
                severity=ErrorSeverity.WARNING,
                category=ErrorCategory.NETWORK,
                photo=photo_context,
                album=album_context,
                file_path=file_path_context,
                recovery_suggestion=recovery_suggestion or "Check internet connection and retry",
            )
            self.logger.log_error_details(error)
            return default_return
            
        except TimeoutError as e:
            error = ErrorDetails.from_exception(
                error_id=f"{error_id}_timeout",
                exception=e,
                severity=ErrorSeverity.WARNING,
                category=ErrorCategory.NETWORK,
                photo=photo_context,
                album=album_context,
                file_path=file_path_context,
                recovery_suggestion=recovery_suggestion or "Retry operation or increase timeout",
            )
            self.logger.log_error_details(error)
            return default_return
            
        except Exception as e:
            # Catch-all for unexpected errors
            error = ErrorDetails.from_exception(
                error_id=f"{error_id}_unexpected",
                exception=e,
                severity=ErrorSeverity.ERROR,
                category=ErrorCategory.UNKNOWN,
                photo=photo_context,
                album=album_context,
                file_path=file_path_context,
                recovery_suggestion=recovery_suggestion or "Review error details and contact support",
            )
            self.logger.log_error_details(error)
            return default_return
    
    def safe_photo_operation(
        self, 
        photo: Photo, 
        operation: Callable[[Photo], T], 
        operation_name: str,
        recovery_suggestion: Optional[str] = None
    ) -> Optional[T]:
        """Safely execute photo-specific operation."""
        return self.safe_execute(
            operation=lambda: operation(photo),
            error_id=f"photo_{operation_name}",
            category=ErrorCategory.ICLOUD_API,
            photo_context=photo,
            recovery_suggestion=recovery_suggestion,
        )
    
    def safe_file_operation(
        self, 
        file_path: DataPath, 
        operation: Callable[[DataPath], T], 
        operation_name: str,
        recovery_suggestion: Optional[str] = None
    ) -> Optional[T]:
        """Safely execute file-specific operation."""
        return self.safe_execute(
            operation=lambda: operation(file_path),
            error_id=f"file_{operation_name}",
            category=ErrorCategory.FILE_SYSTEM,
            file_path_context=file_path,
            recovery_suggestion=recovery_suggestion,
        )


class ProductionErrorReporter:
    """Production error reporting and analysis."""
    
    def __init__(self, logger: ProductionLogger) -> None:
        self.logger = logger
    
    def generate_error_report(self) -> str:
        """Generate comprehensive error report."""
        summary = self.logger.get_error_summary()
        
        if summary.total_errors == 0:
            return "✅ No errors encountered during operation."
        
        report_lines = [
            "📊 Error Summary",
            "=" * 50,
            f"Total errors: {summary.total_errors}",
            f"Critical errors: {summary.critical_errors}",
            f"Recoverable errors: {summary.recoverable_errors}",
            f"Success rate: {summary.success_rate:.1f}%",
            "",
            "Errors by severity:",
        ]
        
        for severity, count in summary.errors_by_severity.items():
            report_lines.append(f"  {severity.value}: {count}")
        
        report_lines.extend([
            "",
            "Errors by category:",
        ])
        
        for category, count in summary.errors_by_category.items():
            report_lines.append(f"  {category.value}: {count}")
        
        # Add critical error details
        critical_errors = [e for e in summary.error_details if e.severity == ErrorSeverity.CRITICAL]
        if critical_errors:
            report_lines.extend([
                "",
                "🚨 Critical Errors:",
                "-" * 30,
            ])
            
            for error in critical_errors[:5]:  # Show first 5 critical errors
                report_lines.extend([
                    f"ID: {error.error_id}",
                    f"Message: {error.message}",
                    f"Context: {error.context_summary}",
                    f"Recovery: {error.recovery_suggestion or 'none'}",
                    "",
                ])
        
        return "\n".join(report_lines)
    
    def get_recovery_recommendations(self) -> list[str]:
        """Get actionable recovery recommendations."""
        summary = self.logger.get_error_summary()
        recommendations = []
        
        # Network issues
        network_errors = summary.errors_by_category.get(ErrorCategory.NETWORK, 0)
        if network_errors > 0:
            recommendations.append(
                f"🌐 {network_errors} network errors detected. "
                "Check internet connection and consider using --retry-failed flag."
            )
        
        # Permission issues
        permission_errors = summary.errors_by_category.get(ErrorCategory.PERMISSION, 0)
        if permission_errors > 0:
            recommendations.append(
                f"🔒 {permission_errors} permission errors detected. "
                "Check file/directory permissions and disk space."
            )
        
        # File system issues
        fs_errors = summary.errors_by_category.get(ErrorCategory.FILE_SYSTEM, 0)
        if fs_errors > 0:
            recommendations.append(
                f"💾 {fs_errors} file system errors detected. "
                "Check disk space and file system integrity."
            )
        
        # Critical errors
        if summary.has_critical_errors:
            recommendations.append(
                f"🚨 {summary.critical_errors} critical errors require immediate attention. "
                "Review error details and consider contacting support."
            )
        
        return recommendations


# Factory functions
def create_production_logger(name: str = "icloudpd", level: LogLevel = "info") -> ProductionLogger:
    """Factory function to create a production logger."""
    return ProductionLogger(name, level)


def create_safe_wrapper(logger: ProductionLogger) -> SafeOperationWrapper:
    """Factory function to create a safe operation wrapper."""
    return SafeOperationWrapper(logger)


def create_error_reporter(logger: ProductionLogger) -> ProductionErrorReporter:
    """Factory function to create an error reporter."""
    return ProductionErrorReporter(logger)