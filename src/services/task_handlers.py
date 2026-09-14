"""
Task handlers for different step types.

Each handler implements the logic for a specific task type.
The registry allows for dynamic handler registration.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Optional, Type
import json

from src.config import get_config
from .url_guard import validate_url

logger = logging.getLogger(__name__)


class TaskHandler(ABC):
    """
    Base class for task handlers.
    
    Each task type (http_request, data_transform, etc.) implements
    this interface to provide execution logic.
    """
    
    @property
    @abstractmethod
    def task_type(self) -> str:
        """Return the task type this handler processes."""
        pass
    
    @abstractmethod
    def execute(
        self,
        step_config: Dict[str, Any],
        input_data: Dict[str, Any],
        timeout: int = 300,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute the task.
        
        Args:
            step_config: Configuration specific to this step
            input_data: Input data from workflow/previous steps
            timeout: Maximum execution time in seconds
            
        Returns:
            Output data from the step, or None if no output
            
        Raises:
            Exception: On any failure
        """
        pass
    
    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate step configuration. Override in subclasses."""
        return True


class TaskHandlerRegistry:
    """
    Registry for task handlers.
    
    Allows dynamic registration and lookup of handlers by task type.
    """
    
    def __init__(self):
        self._handlers: Dict[str, TaskHandler] = {}
    
    def register(self, handler: TaskHandler) -> None:
        """Register a task handler."""
        self._handlers[handler.task_type] = handler
        logger.info(f"Registered handler for task type: {handler.task_type}")
    
    def get_handler(self, task_type: str) -> Optional[TaskHandler]:
        """Get a handler for a task type."""
        return self._handlers.get(task_type)
    
    def list_task_types(self) -> list:
        """List all registered task types."""
        return list(self._handlers.keys())


# ============================================
# BUILT-IN TASK HANDLERS
# ============================================

class HttpRequestHandler(TaskHandler):
    """
    Handler for HTTP request tasks.

    Config schema:
    {
        "url": "https://api.example.com/endpoint",
        "method": "GET" | "POST" | "PUT" | "DELETE",
        "headers": {"key": "value"},
        "body": {...} | null,
        "expected_status": [200, 201]
    }

    URLs are validated against a hostname allowlist before the request is made.
    See src/services/url_guard.py for what is enforced and why.
    """

    def __init__(self, allowed_hosts: Optional[Iterable[str]] = None):
        """
        Args:
            allowed_hosts: Permitted hostnames. Defaults to Config.ALLOWED_HTTP_HOSTS.
                Injected explicitly in tests so validation runs without network access.
        """
        self._allowed_hosts = allowed_hosts

    @property
    def allowed_hosts(self) -> Iterable[str]:
        """Resolve the allowlist lazily so config is read at call time, not import time."""
        if self._allowed_hosts is None:
            return get_config().ALLOWED_HTTP_HOSTS
        return self._allowed_hosts

    @property
    def task_type(self) -> str:
        return "http_request"

    def execute(
        self,
        step_config: Dict[str, Any],
        input_data: Dict[str, Any],
        timeout: int = 300,
    ) -> Optional[Dict[str, Any]]:
        import requests

        url = step_config.get("url")
        method = step_config.get("method", "GET").upper()
        headers = step_config.get("headers", {})
        body = step_config.get("body")
        expected_status = step_config.get("expected_status", [200, 201, 204])

        # Support template substitution in URL
        if url and "{" in url:
            url = url.format(**input_data)

        # Validate after substitution, so a template cannot smuggle a blocked
        # host past the check via input data. Raises BlockedUrlError, which the
        # orchestrator handles on its normal step-failure path.
        validate_url(url, self.allowed_hosts)

        logger.info(f"Making {method} request to {url}")

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=body if body else None,
            timeout=timeout,
        )
        
        if response.status_code not in expected_status:
            raise Exception(
                f"HTTP request failed with status {response.status_code}: {response.text}"
            )
        
        try:
            response_data = response.json()
        except json.JSONDecodeError:
            response_data = {"text": response.text}
        
        return {
            "status_code": response.status_code,
            "response": response_data,
        }


class DataTransformHandler(TaskHandler):
    """
    Handler for data transformation tasks.
    
    Config schema:
    {
        "transforms": [
            {"type": "rename", "from": "old_key", "to": "new_key"},
            {"type": "extract", "key": "nested.path", "as": "new_key"},
            {"type": "set", "key": "key", "value": "static_value"},
            {"type": "delete", "keys": ["key1", "key2"]}
        ]
    }
    """
    
    @property
    def task_type(self) -> str:
        return "data_transform"
    
    def execute(
        self,
        step_config: Dict[str, Any],
        input_data: Dict[str, Any],
        timeout: int = 300,
    ) -> Optional[Dict[str, Any]]:
        transforms = step_config.get("transforms", [])
        result = input_data.copy()
        
        for transform in transforms:
            transform_type = transform.get("type")
            
            if transform_type == "rename":
                from_key = transform["from"]
                to_key = transform["to"]
                if from_key in result:
                    result[to_key] = result.pop(from_key)
            
            elif transform_type == "extract":
                key_path = transform["key"]
                as_key = transform.get("as", key_path.split(".")[-1])
                value = self._get_nested(result, key_path)
                if value is not None:
                    result[as_key] = value
            
            elif transform_type == "set":
                result[transform["key"]] = transform["value"]
            
            elif transform_type == "delete":
                for key in transform.get("keys", []):
                    result.pop(key, None)
        
        return result
    
    def _get_nested(self, data: Dict, path: str) -> Any:
        """Get a nested value from a dict using dot notation."""
        keys = path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value


class DelayHandler(TaskHandler):
    """
    Handler for delay/wait tasks.
    
    Config schema:
    {
        "seconds": 5
    }
    """
    
    @property
    def task_type(self) -> str:
        return "delay"
    
    def execute(
        self,
        step_config: Dict[str, Any],
        input_data: Dict[str, Any],
        timeout: int = 300,
    ) -> Optional[Dict[str, Any]]:
        seconds = step_config.get("seconds", 1)
        logger.info(f"Delaying for {seconds} seconds")
        time.sleep(seconds)
        return {"delayed_seconds": seconds}


class ConditionalHandler(TaskHandler):
    """
    Handler for conditional logic.
    
    Config schema:
    {
        "condition": {
            "field": "some_key",
            "operator": "eq" | "ne" | "gt" | "lt" | "contains" | "exists",
            "value": "expected_value"
        },
        "on_true": {"result": "condition_met"},
        "on_false": {"result": "condition_not_met"}
    }
    """
    
    @property
    def task_type(self) -> str:
        return "conditional"
    
    def execute(
        self,
        step_config: Dict[str, Any],
        input_data: Dict[str, Any],
        timeout: int = 300,
    ) -> Optional[Dict[str, Any]]:
        condition = step_config.get("condition", {})
        field = condition.get("field")
        operator = condition.get("operator", "eq")
        expected = condition.get("value")
        
        actual = input_data.get(field)
        
        result = False
        
        if operator == "eq":
            result = actual == expected
        elif operator == "ne":
            result = actual != expected
        elif operator == "gt":
            result = actual > expected
        elif operator == "lt":
            result = actual < expected
        elif operator == "contains":
            result = expected in actual if actual else False
        elif operator == "exists":
            result = field in input_data
        
        output = step_config.get("on_true" if result else "on_false", {})
        return {"condition_result": result, **output}


class LogHandler(TaskHandler):
    """
    Handler for logging tasks.
    
    Config schema:
    {
        "message": "Log message with {placeholder}",
        "level": "info" | "warning" | "error"
    }
    """
    
    @property
    def task_type(self) -> str:
        return "log"
    
    def execute(
        self,
        step_config: Dict[str, Any],
        input_data: Dict[str, Any],
        timeout: int = 300,
    ) -> Optional[Dict[str, Any]]:
        message = step_config.get("message", "Log step executed")
        level = step_config.get("level", "info")
        
        # Template substitution
        try:
            message = message.format(**input_data)
        except KeyError:
            pass
        
        log_func = getattr(logger, level, logger.info)
        log_func(f"[WorkflowLog] {message}")
        
        return {"logged_message": message, "level": level}


def create_default_registry() -> TaskHandlerRegistry:
    """Create a registry with all built-in handlers."""
    registry = TaskHandlerRegistry()
    
    registry.register(HttpRequestHandler())
    registry.register(DataTransformHandler())
    registry.register(DelayHandler())
    registry.register(ConditionalHandler())
    registry.register(LogHandler())
    
    return registry
