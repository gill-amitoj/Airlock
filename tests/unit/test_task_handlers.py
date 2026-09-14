"""
Unit tests for task handlers.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.services.task_handlers import (
    TaskHandlerRegistry,
    HttpRequestHandler,
    DataTransformHandler,
    DelayHandler,
    ConditionalHandler,
    LogHandler,
    create_default_registry,
)
from src.services.url_guard import BlockedUrlError


class TestTaskHandlerRegistry:
    """Tests for TaskHandlerRegistry."""
    
    def test_register_handler(self):
        """Test registering a handler."""
        registry = TaskHandlerRegistry()
        handler = LogHandler()
        
        registry.register(handler)
        
        assert registry.get_handler("log") == handler
    
    def test_get_unregistered_handler(self):
        """Test getting an unregistered handler returns None."""
        registry = TaskHandlerRegistry()
        
        assert registry.get_handler("nonexistent") is None
    
    def test_list_task_types(self):
        """Test listing registered task types."""
        registry = create_default_registry()
        
        types = registry.list_task_types()
        
        assert "http_request" in types
        assert "data_transform" in types
        assert "delay" in types
        assert "conditional" in types
        assert "log" in types


class TestLogHandler:
    """Tests for LogHandler."""
    
    def test_task_type(self):
        """Test task type property."""
        handler = LogHandler()
        assert handler.task_type == "log"
    
    def test_execute_basic(self):
        """Test basic log execution."""
        handler = LogHandler()
        
        result = handler.execute(
            step_config={"message": "Test message"},
            input_data={},
        )
        
        assert result["logged_message"] == "Test message"
        assert result["level"] == "info"
    
    def test_execute_with_template(self):
        """Test log with template substitution."""
        handler = LogHandler()
        
        result = handler.execute(
            step_config={"message": "Hello, {name}!"},
            input_data={"name": "World"},
        )
        
        assert result["logged_message"] == "Hello, World!"
    
    def test_execute_with_level(self):
        """Test log with custom level."""
        handler = LogHandler()
        
        result = handler.execute(
            step_config={"message": "Warning!", "level": "warning"},
            input_data={},
        )
        
        assert result["level"] == "warning"


class TestDelayHandler:
    """Tests for DelayHandler."""
    
    def test_task_type(self):
        """Test task type property."""
        handler = DelayHandler()
        assert handler.task_type == "delay"
    
    @patch("time.sleep")
    def test_execute(self, mock_sleep):
        """Test delay execution."""
        handler = DelayHandler()
        
        result = handler.execute(
            step_config={"seconds": 5},
            input_data={},
        )
        
        mock_sleep.assert_called_once_with(5)
        assert result["delayed_seconds"] == 5


class TestConditionalHandler:
    """Tests for ConditionalHandler."""
    
    def test_task_type(self):
        """Test task type property."""
        handler = ConditionalHandler()
        assert handler.task_type == "conditional"
    
    def test_execute_eq_true(self):
        """Test equality condition - true."""
        handler = ConditionalHandler()
        
        result = handler.execute(
            step_config={
                "condition": {
                    "field": "status",
                    "operator": "eq",
                    "value": "active",
                },
                "on_true": {"action": "proceed"},
                "on_false": {"action": "skip"},
            },
            input_data={"status": "active"},
        )
        
        assert result["condition_result"] is True
        assert result["action"] == "proceed"
    
    def test_execute_eq_false(self):
        """Test equality condition - false."""
        handler = ConditionalHandler()
        
        result = handler.execute(
            step_config={
                "condition": {
                    "field": "status",
                    "operator": "eq",
                    "value": "active",
                },
                "on_true": {"action": "proceed"},
                "on_false": {"action": "skip"},
            },
            input_data={"status": "inactive"},
        )
        
        assert result["condition_result"] is False
        assert result["action"] == "skip"
    
    def test_execute_gt(self):
        """Test greater than condition."""
        handler = ConditionalHandler()
        
        result = handler.execute(
            step_config={
                "condition": {
                    "field": "count",
                    "operator": "gt",
                    "value": 5,
                },
            },
            input_data={"count": 10},
        )
        
        assert result["condition_result"] is True
    
    def test_execute_contains(self):
        """Test contains condition."""
        handler = ConditionalHandler()
        
        result = handler.execute(
            step_config={
                "condition": {
                    "field": "tags",
                    "operator": "contains",
                    "value": "important",
                },
            },
            input_data={"tags": ["important", "urgent"]},
        )
        
        assert result["condition_result"] is True
    
    def test_execute_exists(self):
        """Test exists condition."""
        handler = ConditionalHandler()
        
        result = handler.execute(
            step_config={
                "condition": {
                    "field": "optional_field",
                    "operator": "exists",
                },
            },
            input_data={"optional_field": "value"},
        )
        
        assert result["condition_result"] is True


class TestDataTransformHandler:
    """Tests for DataTransformHandler."""
    
    def test_task_type(self):
        """Test task type property."""
        handler = DataTransformHandler()
        assert handler.task_type == "data_transform"
    
    def test_execute_rename(self):
        """Test rename transformation."""
        handler = DataTransformHandler()
        
        result = handler.execute(
            step_config={
                "transforms": [
                    {"type": "rename", "from": "old_name", "to": "new_name"}
                ]
            },
            input_data={"old_name": "value"},
        )
        
        assert "old_name" not in result
        assert result["new_name"] == "value"
    
    def test_execute_set(self):
        """Test set transformation."""
        handler = DataTransformHandler()
        
        result = handler.execute(
            step_config={
                "transforms": [
                    {"type": "set", "key": "new_key", "value": "new_value"}
                ]
            },
            input_data={},
        )
        
        assert result["new_key"] == "new_value"
    
    def test_execute_delete(self):
        """Test delete transformation."""
        handler = DataTransformHandler()
        
        result = handler.execute(
            step_config={
                "transforms": [
                    {"type": "delete", "keys": ["key1", "key2"]}
                ]
            },
            input_data={"key1": "v1", "key2": "v2", "key3": "v3"},
        )
        
        assert "key1" not in result
        assert "key2" not in result
        assert result["key3"] == "v3"
    
    def test_execute_extract_nested(self):
        """Test extract transformation with nested path."""
        handler = DataTransformHandler()
        
        result = handler.execute(
            step_config={
                "transforms": [
                    {"type": "extract", "key": "response.data.id", "as": "extracted_id"}
                ]
            },
            input_data={
                "response": {
                    "data": {
                        "id": "12345"
                    }
                }
            },
        )
        
        assert result["extracted_id"] == "12345"
    
    def test_execute_multiple_transforms(self):
        """Test multiple transformations in sequence."""
        handler = DataTransformHandler()
        
        result = handler.execute(
            step_config={
                "transforms": [
                    {"type": "rename", "from": "input", "to": "processed"},
                    {"type": "set", "key": "status", "value": "transformed"},
                    {"type": "delete", "keys": ["temp"]},
                ]
            },
            input_data={"input": "data", "temp": "remove_me"},
        )
        
        assert result["processed"] == "data"
        assert result["status"] == "transformed"
        assert "temp" not in result
        assert "input" not in result


class TestHttpRequestHandler:
    """
    Tests for HttpRequestHandler.

    These tests call api.example.com, which is not on the production allowlist,
    so the handler is constructed with that host injected. resolve_hostname is
    patched class-wide so validation never performs a DNS lookup.
    """

    ALLOWED = ["api.example.com"]

    @pytest.fixture(autouse=True)
    def _stub_dns(self):
        """Resolve every host to a routable address, with no network access."""
        with patch(
            "src.services.url_guard.resolve_hostname",
            return_value=["93.184.216.34"],
        ) as mock_resolve:
            yield mock_resolve

    def test_task_type(self):
        """Test task type property."""
        handler = HttpRequestHandler()
        assert handler.task_type == "http_request"

    @patch("requests.request")
    def test_execute_get_request(self, mock_request):
        """Test GET request execution."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}
        mock_request.return_value = mock_response
        
        handler = HttpRequestHandler(allowed_hosts=self.ALLOWED)
        result = handler.execute(
            step_config={
                "url": "https://api.example.com/test",
                "method": "GET",
            },
            input_data={},
        )
        
        mock_request.assert_called_once()
        assert result["status_code"] == 200
        assert result["response"]["data"] == "test"
    
    @patch("requests.request")
    def test_execute_post_request(self, mock_request):
        """Test POST request execution."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "123"}
        mock_request.return_value = mock_response
        
        handler = HttpRequestHandler(allowed_hosts=self.ALLOWED)
        result = handler.execute(
            step_config={
                "url": "https://api.example.com/create",
                "method": "POST",
                "body": {"name": "test"},
            },
            input_data={},
        )
        
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args.kwargs["method"] == "POST"
        assert call_args.kwargs["json"] == {"name": "test"}
    
    @patch("requests.request")
    def test_execute_with_url_template(self, mock_request):
        """Test URL template substitution."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_request.return_value = mock_response
        
        handler = HttpRequestHandler(allowed_hosts=self.ALLOWED)
        handler.execute(
            step_config={
                "url": "https://api.example.com/users/{user_id}",
                "method": "GET",
            },
            input_data={"user_id": "456"},
        )
        
        call_args = mock_request.call_args
        assert call_args.kwargs["url"] == "https://api.example.com/users/456"
    
    @patch("requests.request")
    def test_execute_unexpected_status(self, mock_request):
        """Test handling of unexpected status codes."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_request.return_value = mock_response
        
        handler = HttpRequestHandler(allowed_hosts=self.ALLOWED)

        with pytest.raises(Exception, match="HTTP request failed"):
            handler.execute(
                step_config={
                    "url": "https://api.example.com/error",
                    "method": "GET",
                },
                input_data={},
            )

    @patch("requests.request")
    def test_blocked_host_raises_before_request(self, mock_request):
        """A non-allowlisted host is refused and no request is sent."""
        handler = HttpRequestHandler(allowed_hosts=self.ALLOWED)

        with pytest.raises(BlockedUrlError, match="not in the allowlist"):
            handler.execute(
                step_config={
                    "url": "https://evil.example.com/exfiltrate",
                    "method": "GET",
                },
                input_data={},
            )

        mock_request.assert_not_called()

    @patch("requests.request")
    def test_private_address_raises_before_request(self, mock_request, _stub_dns):
        """An allowlisted host resolving internally is refused before the request."""
        _stub_dns.return_value = ["169.254.169.254"]
        handler = HttpRequestHandler(allowed_hosts=self.ALLOWED)

        with pytest.raises(BlockedUrlError, match="non-public address"):
            handler.execute(
                step_config={
                    "url": "https://api.example.com/meta",
                    "method": "GET",
                },
                input_data={},
            )

        mock_request.assert_not_called()

    @patch("requests.request")
    def test_template_cannot_smuggle_blocked_host(self, mock_request):
        """
        Validation runs after substitution, so input data cannot inject a host.

        This is the ordering that matters: validating the raw template would
        check 'api.example.com' and then request somewhere else entirely.
        """
        handler = HttpRequestHandler(allowed_hosts=self.ALLOWED)

        with pytest.raises(BlockedUrlError, match="not in the allowlist"):
            handler.execute(
                step_config={
                    "url": "https://{host}/data",
                    "method": "GET",
                },
                input_data={"host": "evil.example.com"},
            )

        mock_request.assert_not_called()

    @patch("requests.request")
    def test_default_allowlist_comes_from_config(self, mock_request):
        """With no injection, the handler enforces the configured allowlist."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"fact": "cats sleep a lot"}
        mock_request.return_value = mock_response

        handler = HttpRequestHandler()

        # On the default allowlist.
        handler.execute(
            step_config={"url": "https://catfact.ninja/fact", "method": "GET"},
            input_data={},
        )

        # Not on it.
        with pytest.raises(BlockedUrlError):
            handler.execute(
                step_config={"url": "https://evil.example.com/", "method": "GET"},
                input_data={},
            )
