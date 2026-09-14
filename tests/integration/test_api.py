"""
Integration tests for API endpoints.
"""

import pytest
import json
from unittest.mock import patch, MagicMock
from uuid import uuid4

from src.domain import WorkflowStatus, ExecutionStatus


class TestWorkflowEndpoints:
    """Integration tests for workflow API endpoints."""
    
    def test_create_workflow(self, client):
        """Test POST /api/v1/workflows."""
        with patch('src.api.routes.get_workflow_service') as mock_service:
            mock_workflow = MagicMock()
            mock_workflow.id = uuid4()
            mock_workflow.name = "test-workflow"
            mock_workflow.description = "Test"
            mock_workflow.status = WorkflowStatus.DRAFT
            mock_workflow.version = 1
            mock_workflow.steps = []
            mock_workflow.metadata = {}
            mock_workflow.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_workflow.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.create_workflow.return_value = mock_workflow
            
            response = client.post(
                "/api/v1/workflows",
                data=json.dumps({"name": "test-workflow", "description": "Test"}),
                content_type="application/json",
            )
            
            assert response.status_code == 201
            data = json.loads(response.data)
            assert data["name"] == "test-workflow"
    
    def test_create_workflow_missing_name(self, client):
        """Test POST /api/v1/workflows without name."""
        response = client.post(
            "/api/v1/workflows",
            data=json.dumps({"description": "Test"}),
            content_type="application/json",
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "name" in data["error"].lower()
    
    def test_create_workflow_empty_body(self, client):
        """Test POST /api/v1/workflows with empty body."""
        response = client.post(
            "/api/v1/workflows",
            data=json.dumps(None),
            content_type="application/json",
        )
        
        assert response.status_code == 400
    
    def test_get_workflow(self, client):
        """Test GET /api/v1/workflows/<id>."""
        workflow_id = uuid4()
        
        with patch('src.api.routes.get_workflow_service') as mock_service:
            mock_workflow = MagicMock()
            mock_workflow.id = workflow_id
            mock_workflow.name = "test"
            mock_workflow.description = ""
            mock_workflow.status = WorkflowStatus.DRAFT
            mock_workflow.version = 1
            mock_workflow.steps = []
            mock_workflow.metadata = {}
            mock_workflow.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_workflow.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.get_workflow.return_value = mock_workflow
            
            response = client.get(f"/api/v1/workflows/{workflow_id}")
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["id"] == str(workflow_id)
    
    def test_get_workflow_not_found(self, client):
        """Test GET /api/v1/workflows/<id> with non-existent ID."""
        from src.services.workflow_service import WorkflowNotFoundError
        
        with patch('src.api.routes.get_workflow_service') as mock_service:
            mock_service.return_value.get_workflow.side_effect = WorkflowNotFoundError("Not found")
            
            response = client.get(f"/api/v1/workflows/{uuid4()}")
            
            assert response.status_code == 404
    
    def test_get_workflow_invalid_id(self, client):
        """Test GET /api/v1/workflows/<id> with invalid UUID."""
        response = client.get("/api/v1/workflows/not-a-uuid")
        
        assert response.status_code == 400
    
    def test_list_workflows(self, client):
        """Test GET /api/v1/workflows."""
        with patch('src.api.routes.get_workflow_service') as mock_service:
            mock_service.return_value.list_workflows.return_value = []
            
            response = client.get("/api/v1/workflows")
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert "workflows" in data
            assert "count" in data
    
    def test_list_workflows_with_filter(self, client):
        """Test GET /api/v1/workflows with status filter."""
        with patch('src.api.routes.get_workflow_service') as mock_service:
            mock_service.return_value.list_workflows.return_value = []
            
            response = client.get("/api/v1/workflows?status=active&limit=50&offset=10")
            
            assert response.status_code == 200
            mock_service.return_value.list_workflows.assert_called_once_with(
                status=WorkflowStatus.ACTIVE,
                limit=50,
                offset=10,
            )
    
    def test_add_step(self, client):
        """Test POST /api/v1/workflows/<id>/steps."""
        workflow_id = uuid4()
        step_id = uuid4()
        
        with patch('src.api.routes.get_workflow_service') as mock_service:
            mock_step = MagicMock()
            mock_step.id = step_id
            mock_step.workflow_id = workflow_id
            mock_step.name = "test-step"
            mock_step.task_type = "log"
            mock_step.step_order = 0
            mock_step.config = {}
            mock_step.timeout_seconds = 300
            mock_step.max_retries = 3
            mock_step.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_step.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.add_step.return_value = mock_step
            
            response = client.post(
                f"/api/v1/workflows/{workflow_id}/steps",
                data=json.dumps({
                    "name": "test-step",
                    "task_type": "log",
                    "step_order": 0,
                }),
                content_type="application/json",
            )
            
            assert response.status_code == 201
            data = json.loads(response.data)
            assert data["name"] == "test-step"
    
    def test_activate_workflow(self, client):
        """Test POST /api/v1/workflows/<id>/activate."""
        workflow_id = uuid4()
        
        with patch('src.api.routes.get_workflow_service') as mock_service:
            mock_workflow = MagicMock()
            mock_workflow.id = workflow_id
            mock_workflow.name = "test"
            mock_workflow.description = ""
            mock_workflow.status = WorkflowStatus.ACTIVE
            mock_workflow.version = 1
            mock_workflow.steps = []
            mock_workflow.metadata = {}
            mock_workflow.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_workflow.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.activate_workflow.return_value = mock_workflow
            
            response = client.post(f"/api/v1/workflows/{workflow_id}/activate")
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "active"


class TestExecutionEndpoints:
    """Integration tests for execution API endpoints."""
    
    def test_trigger_execution(self, client):
        """Test POST /api/v1/executions."""
        workflow_id = uuid4()
        execution_id = uuid4()
        
        with patch('src.api.routes.get_execution_service') as mock_service, \
             patch('src.api.routes.get_queue') as mock_queue:
            
            mock_execution = MagicMock()
            mock_execution.id = execution_id
            mock_execution.workflow_id = workflow_id
            mock_execution.idempotency_key = "test-key"
            mock_execution.status = ExecutionStatus.PENDING
            mock_execution.current_step_order = 0
            mock_execution.retry_count = 0
            mock_execution.max_retries = 3
            mock_execution.input_data = {}
            mock_execution.output_data = None
            mock_execution.error_message = None
            mock_execution.scheduled_at = None
            mock_execution.started_at = None
            mock_execution.completed_at = None
            mock_execution.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.create_execution.return_value = mock_execution
            mock_queue.return_value.enqueue.return_value = MagicMock()
            
            response = client.post(
                "/api/v1/executions",
                data=json.dumps({
                    "workflow_id": str(workflow_id),
                    "idempotency_key": "test-key",
                    "input_data": {"key": "value"},
                }),
                content_type="application/json",
            )
            
            assert response.status_code == 201
            data = json.loads(response.data)
            assert data["status"] == "pending"
    
    def test_trigger_execution_missing_fields(self, client):
        """Test POST /api/v1/executions with missing required fields."""
        response = client.post(
            "/api/v1/executions",
            data=json.dumps({"workflow_id": str(uuid4())}),
            content_type="application/json",
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "idempotency_key" in data["error"].lower()
    
    def test_trigger_execution_duplicate(self, client):
        """Test POST /api/v1/executions with duplicate idempotency key."""
        from src.services.execution_service import DuplicateExecutionError
        
        workflow_id = uuid4()
        execution_id = uuid4()
        
        with patch('src.api.routes.get_execution_service') as mock_service:
            mock_execution = MagicMock()
            mock_execution.id = execution_id
            mock_execution.workflow_id = workflow_id
            mock_execution.idempotency_key = "test-key"
            mock_execution.status = ExecutionStatus.RUNNING
            mock_execution.current_step_order = 1
            mock_execution.retry_count = 0
            mock_execution.max_retries = 3
            mock_execution.input_data = {}
            mock_execution.output_data = None
            mock_execution.error_message = None
            mock_execution.scheduled_at = None
            mock_execution.started_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.completed_at = None
            mock_execution.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.create_execution.side_effect = DuplicateExecutionError(mock_execution)
            
            response = client.post(
                "/api/v1/executions",
                data=json.dumps({
                    "workflow_id": str(workflow_id),
                    "idempotency_key": "test-key",
                }),
                content_type="application/json",
            )
            
            # Should return existing execution with 200
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "running"
    
    def test_get_execution(self, client):
        """Test GET /api/v1/executions/<id>."""
        execution_id = uuid4()
        
        with patch('src.api.routes.get_execution_service') as mock_service:
            mock_execution = MagicMock()
            mock_execution.id = execution_id
            mock_execution.workflow_id = uuid4()
            mock_execution.idempotency_key = "test"
            mock_execution.status = ExecutionStatus.COMPLETED
            mock_execution.current_step_order = 2
            mock_execution.retry_count = 0
            mock_execution.max_retries = 3
            mock_execution.input_data = {"input": "data"}
            mock_execution.output_data = {"result": "success"}
            mock_execution.error_message = None
            mock_execution.scheduled_at = None
            mock_execution.started_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.completed_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.get_execution.return_value = mock_execution
            
            response = client.get(f"/api/v1/executions/{execution_id}")
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "completed"
            assert data["output_data"] == {"result": "success"}
    
    def test_retry_execution(self, client):
        """Test POST /api/v1/executions/<id>/retry."""
        execution_id = uuid4()
        
        with patch('src.api.routes.get_execution_service') as mock_service, \
             patch('src.api.routes.get_queue') as mock_queue:
            
            mock_execution = MagicMock()
            mock_execution.id = execution_id
            mock_execution.workflow_id = uuid4()
            mock_execution.idempotency_key = "test"
            mock_execution.status = ExecutionStatus.RETRYING
            mock_execution.current_step_order = 1
            mock_execution.retry_count = 1
            mock_execution.max_retries = 3
            mock_execution.input_data = {}
            mock_execution.output_data = None
            mock_execution.error_message = None
            mock_execution.scheduled_at = None
            mock_execution.started_at = None
            mock_execution.completed_at = None
            mock_execution.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.retry_execution.return_value = mock_execution
            mock_queue.return_value.enqueue.return_value = MagicMock()
            
            response = client.post(f"/api/v1/executions/{execution_id}/retry")
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "retrying"
    
    def test_cancel_execution(self, client):
        """Test POST /api/v1/executions/<id>/cancel."""
        execution_id = uuid4()
        
        with patch('src.api.routes.get_execution_service') as mock_service:
            mock_execution = MagicMock()
            mock_execution.id = execution_id
            mock_execution.workflow_id = uuid4()
            mock_execution.idempotency_key = "test"
            mock_execution.status = ExecutionStatus.CANCELLED
            mock_execution.current_step_order = 1
            mock_execution.retry_count = 0
            mock_execution.max_retries = 3
            mock_execution.input_data = {}
            mock_execution.output_data = None
            mock_execution.error_message = None
            mock_execution.scheduled_at = None
            mock_execution.started_at = None
            mock_execution.completed_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.created_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            mock_execution.updated_at = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.cancel_execution.return_value = mock_execution
            
            response = client.post(f"/api/v1/executions/{execution_id}/cancel")
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "cancelled"
    
    def test_get_execution_logs(self, client):
        """Test GET /api/v1/executions/<id>/logs."""
        execution_id = uuid4()
        
        with patch('src.api.routes.get_execution_service') as mock_service:
            mock_log = MagicMock()
            mock_log.id = uuid4()
            mock_log.execution_id = execution_id
            mock_log.step_execution_id = None
            mock_log.level = MagicMock(value="info")
            mock_log.message = "Test log"
            mock_log.details = {}
            mock_log.timestamp = MagicMock(isoformat=lambda: "2024-01-01T00:00:00")
            
            mock_service.return_value.get_execution_logs.return_value = [mock_log]
            
            response = client.get(f"/api/v1/executions/{execution_id}/logs")
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert len(data["logs"]) == 1
            assert data["logs"][0]["message"] == "Test log"


class TestHealthEndpoint:
    """Tests for health check endpoint."""
    
    def test_health_check_healthy(self, client, mock_db):
        """Test /health when all services are healthy."""
        mock_db.health_check.return_value = True
        
        # create_app's health_check imports TaskQueue inside the function, so it
        # is resolved from src.worker at call time - that is where it is patched.
        with patch('src.worker.TaskQueue') as mock_queue_class:
            mock_queue_class.return_value.health_check.return_value = True
            
            response = client.get("/health")
            
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "healthy"
    
    def test_health_check_db_unhealthy(self, client, mock_db):
        """Test /health when database is unhealthy."""
        mock_db.health_check.return_value = False
        
        response = client.get("/health")
        
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["status"] == "unhealthy"
        assert data["database"] == "unhealthy"
