"""
API routes for the workflow orchestration engine.

Defines REST endpoints for workflow management and execution.
"""

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from flask import Flask, Blueprint, request, jsonify, g

from src.domain import WorkflowStatus, ExecutionStatus, LogLevel
from src.persistence import (
    Database, WorkflowRepository, ExecutionRepository, LogRepository
)
from src.services import WorkflowService, ExecutionService
from src.services.workflow_service import (
    WorkflowNotFoundError, WorkflowValidationError
)
from src.services.execution_service import (
    ExecutionNotFoundError, ExecutionStateError, DuplicateExecutionError
)
from src.services.llm_service import (
    WorkflowGenerationService,
    LLMUnavailableError,
    LLMTimeoutError,
    LLMValidationError,
)
from src.worker import TaskQueue

logger = logging.getLogger(__name__)

# Create blueprints
workflows_bp = Blueprint("workflows", __name__, url_prefix="/api/v1/workflows")
executions_bp = Blueprint("executions", __name__, url_prefix="/api/v1/executions")
ai_bp = Blueprint("ai", __name__, url_prefix="/api/v1/ai")


def get_db() -> Database:
    """Get database from Flask app config."""
    from flask import current_app
    return current_app.config["DATABASE"]


def get_workflow_service() -> WorkflowService:
    """Get or create WorkflowService."""
    if "workflow_service" not in g:
        db = get_db()
        g.workflow_service = WorkflowService(WorkflowRepository(db))
    return g.workflow_service


def get_execution_service() -> ExecutionService:
    """Get or create ExecutionService."""
    if "execution_service" not in g:
        db = get_db()
        g.execution_service = ExecutionService(
            ExecutionRepository(db),
            WorkflowRepository(db),
            LogRepository(db),
        )
    return g.execution_service


def get_queue() -> TaskQueue:
    """Get or create TaskQueue."""
    if "task_queue" not in g:
        g.task_queue = TaskQueue()
    return g.task_queue


def get_workflow_generation_service() -> WorkflowGenerationService:
    """Get or create WorkflowGenerationService."""
    if "workflow_generation_service" not in g:
        g.workflow_generation_service = WorkflowGenerationService()
    return g.workflow_generation_service


# ============================================
# WORKFLOW ENDPOINTS
# ============================================

@workflows_bp.route("", methods=["POST"])
def create_workflow():
    """
    Create a new workflow definition.
    
    Request body:
    {
        "name": "workflow_name",
        "description": "Workflow description",
        "metadata": {}
    }
    
    Response: 201 Created
    """
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "Request body required"}), 400
    
    name = data.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400
    
    try:
        service = get_workflow_service()
        workflow = service.create_workflow(
            name=name,
            description=data.get("description", ""),
            metadata=data.get("metadata"),
        )
        
        return jsonify(workflow_to_dict(workflow)), 201
        
    except WorkflowValidationError as e:
        return jsonify({"error": str(e)}), 400


@workflows_bp.route("/<workflow_id>", methods=["GET"])
def get_workflow(workflow_id: str):
    """
    Get a workflow by ID.
    
    Response: 200 OK
    """
    try:
        service = get_workflow_service()
        workflow = service.get_workflow(UUID(workflow_id))
        return jsonify(workflow_to_dict(workflow)), 200
        
    except WorkflowNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError:
        return jsonify({"error": "Invalid workflow ID"}), 400


@workflows_bp.route("", methods=["GET"])
def list_workflows():
    """
    List workflows with optional status filter.
    
    Query params:
    - status: Filter by status (draft, active, deprecated, archived)
    - limit: Max results (default 100)
    - offset: Pagination offset (default 0)
    
    Response: 200 OK
    """
    status = request.args.get("status")
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    
    status_enum = WorkflowStatus(status) if status else None
    
    service = get_workflow_service()
    workflows = service.list_workflows(
        status=status_enum,
        limit=limit,
        offset=offset,
    )
    
    return jsonify({
        "workflows": [workflow_to_dict(w) for w in workflows],
        "count": len(workflows),
        "limit": limit,
        "offset": offset,
    }), 200


@workflows_bp.route("/<workflow_id>/steps", methods=["POST"])
def add_workflow_step(workflow_id: str):
    """
    Add a step to a workflow.
    
    Request body:
    {
        "name": "step_name",
        "task_type": "http_request",
        "step_order": 0,
        "config": {},
        "timeout_seconds": 300,
        "max_retries": 3
    }
    
    Response: 201 Created
    """
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "Request body required"}), 400
    
    required_fields = ["name", "task_type", "step_order"]
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"{field} is required"}), 400
    
    try:
        service = get_workflow_service()
        step = service.add_step(
            workflow_id=UUID(workflow_id),
            name=data["name"],
            task_type=data["task_type"],
            step_order=data["step_order"],
            config=data.get("config"),
            timeout_seconds=data.get("timeout_seconds", 300),
            max_retries=data.get("max_retries", 3),
        )
        
        return jsonify(step_to_dict(step)), 201
        
    except WorkflowNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except WorkflowValidationError as e:
        return jsonify({"error": str(e)}), 400
    except ValueError:
        return jsonify({"error": "Invalid workflow ID"}), 400


@workflows_bp.route("/<workflow_id>/activate", methods=["POST"])
def activate_workflow(workflow_id: str):
    """
    Activate a workflow for execution.
    
    Response: 200 OK
    """
    try:
        service = get_workflow_service()
        workflow = service.activate_workflow(UUID(workflow_id))
        return jsonify(workflow_to_dict(workflow)), 200
        
    except WorkflowNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except WorkflowValidationError as e:
        return jsonify({"error": str(e)}), 400
    except ValueError:
        return jsonify({"error": "Invalid workflow ID"}), 400


@workflows_bp.route("/<workflow_id>/deprecate", methods=["POST"])
def deprecate_workflow(workflow_id: str):
    """
    Deprecate a workflow.
    
    Response: 200 OK
    """
    try:
        service = get_workflow_service()
        workflow = service.deprecate_workflow(UUID(workflow_id))
        return jsonify(workflow_to_dict(workflow)), 200
        
    except WorkflowNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except WorkflowValidationError as e:
        return jsonify({"error": str(e)}), 400


# ============================================
# EXECUTION ENDPOINTS
# ============================================

@executions_bp.route("", methods=["POST"])
def trigger_execution():
    """
    Trigger a new workflow execution.
    
    Request body:
    {
        "workflow_id": "uuid",
        "idempotency_key": "unique_key",
        "input_data": {},
        "max_retries": 3,
        "scheduled_at": "2024-01-01T00:00:00Z" (optional)
    }
    
    Response: 201 Created (new) or 200 OK (existing)
    """
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "Request body required"}), 400
    
    workflow_id = data.get("workflow_id")
    idempotency_key = data.get("idempotency_key")
    
    if not workflow_id:
        return jsonify({"error": "workflow_id is required"}), 400
    if not idempotency_key:
        return jsonify({"error": "idempotency_key is required"}), 400
    
    try:
        # Parse scheduled_at if provided
        scheduled_at = None
        if data.get("scheduled_at"):
            scheduled_at = datetime.fromisoformat(
                data["scheduled_at"].replace("Z", "+00:00")
            )
        
        service = get_execution_service()
        execution = service.create_execution(
            workflow_id=UUID(workflow_id),
            idempotency_key=idempotency_key,
            input_data=data.get("input_data"),
            max_retries=data.get("max_retries", 3),
            scheduled_at=scheduled_at,
        )
        
        # Enqueue for async processing
        queue = get_queue()
        delay = 0
        if scheduled_at and scheduled_at > datetime.utcnow():
            delay = int((scheduled_at - datetime.utcnow()).total_seconds())
        
        queue.enqueue(
            execution_id=execution.id,
            idempotency_key=f"{workflow_id}:{idempotency_key}",
            delay_seconds=delay,
        )
        
        return jsonify(execution_to_dict(execution)), 201
        
    except DuplicateExecutionError as e:
        # Return existing execution
        return jsonify(execution_to_dict(e.existing_execution)), 200
    except Exception as e:
        logger.exception(f"Error creating execution: {e}")
        return jsonify({"error": str(e)}), 400


@executions_bp.route("/<execution_id>", methods=["GET"])
def get_execution(execution_id: str):
    """
    Get execution status and details.
    
    Response: 200 OK
    """
    try:
        service = get_execution_service()
        execution = service.get_execution(UUID(execution_id))
        return jsonify(execution_to_dict(execution)), 200
        
    except ExecutionNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError:
        return jsonify({"error": "Invalid execution ID"}), 400


@executions_bp.route("", methods=["GET"])
def list_executions():
    """
    List executions with optional filters.
    
    Query params:
    - workflow_id: Filter by workflow ID
    - status: Filter by status
    - limit: Max results (default 100)
    - offset: Pagination offset (default 0)
    
    Response: 200 OK
    """
    workflow_id = request.args.get("workflow_id")
    status = request.args.get("status")
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    
    workflow_uuid = UUID(workflow_id) if workflow_id else None
    status_enum = ExecutionStatus(status) if status else None
    
    service = get_execution_service()
    executions = service.list_executions(
        workflow_id=workflow_uuid,
        status=status_enum,
        limit=limit,
        offset=offset,
    )
    
    return jsonify({
        "executions": [execution_to_dict(e) for e in executions],
        "count": len(executions),
        "limit": limit,
        "offset": offset,
    }), 200


@executions_bp.route("/<execution_id>/retry", methods=["POST"])
def retry_execution(execution_id: str):
    """
    Retry a failed execution.
    
    Response: 200 OK
    """
    try:
        service = get_execution_service()
        execution = service.retry_execution(UUID(execution_id))
        
        # Enqueue for processing
        queue = get_queue()
        queue.enqueue(execution_id=execution.id)
        
        return jsonify(execution_to_dict(execution)), 200
        
    except ExecutionNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ExecutionStateError as e:
        return jsonify({"error": str(e)}), 400
    except ValueError:
        return jsonify({"error": "Invalid execution ID"}), 400


@executions_bp.route("/<execution_id>/cancel", methods=["POST"])
def cancel_execution(execution_id: str):
    """
    Cancel a running execution.
    
    Response: 200 OK
    """
    try:
        service = get_execution_service()
        execution = service.cancel_execution(UUID(execution_id))
        return jsonify(execution_to_dict(execution)), 200
        
    except ExecutionNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ExecutionStateError as e:
        return jsonify({"error": str(e)}), 400
    except ValueError:
        return jsonify({"error": "Invalid execution ID"}), 400


@executions_bp.route("/<execution_id>/logs", methods=["GET"])
def get_execution_logs(execution_id: str):
    """
    Get logs for an execution.
    
    Query params:
    - level: Filter by log level (debug, info, warning, error)
    - limit: Max results (default 1000)
    - offset: Pagination offset (default 0)
    
    Response: 200 OK
    """
    level = request.args.get("level")
    limit = int(request.args.get("limit", 1000))
    offset = int(request.args.get("offset", 0))
    
    level_enum = LogLevel(level) if level else None
    
    try:
        service = get_execution_service()
        logs = service.get_execution_logs(
            execution_id=UUID(execution_id),
            level=level_enum,
            limit=limit,
            offset=offset,
        )
        
        return jsonify({
            "logs": [log_to_dict(log) for log in logs],
            "count": len(logs),
            "limit": limit,
            "offset": offset,
        }), 200
        
    except ExecutionNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError:
        return jsonify({"error": "Invalid execution ID"}), 400


# ============================================
# SERIALIZATION HELPERS
# ============================================

def workflow_to_dict(workflow) -> dict:
    """Convert Workflow to API response dict."""
    return {
        "id": str(workflow.id),
        "name": workflow.name,
        "description": workflow.description,
        "status": workflow.status.value,
        "version": workflow.version,
        "steps": [step_to_dict(s) for s in workflow.steps],
        "metadata": workflow.metadata,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
    }


def step_to_dict(step) -> dict:
    """Convert WorkflowStep to API response dict."""
    return {
        "id": str(step.id),
        "workflow_id": str(step.workflow_id),
        "name": step.name,
        "task_type": step.task_type,
        "step_order": step.step_order,
        "config": step.config,
        "timeout_seconds": step.timeout_seconds,
        "max_retries": step.max_retries,
        "created_at": step.created_at.isoformat(),
        "updated_at": step.updated_at.isoformat(),
    }


def execution_to_dict(execution) -> dict:
    """Convert WorkflowExecution to API response dict."""
    return {
        "id": str(execution.id),
        "workflow_id": str(execution.workflow_id),
        "idempotency_key": execution.idempotency_key,
        "status": execution.status.value,
        "current_step_order": execution.current_step_order,
        "retry_count": execution.retry_count,
        "max_retries": execution.max_retries,
        "input_data": execution.input_data,
        "output_data": execution.output_data,
        "error_message": execution.error_message,
        "scheduled_at": execution.scheduled_at.isoformat() if execution.scheduled_at else None,
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        "created_at": execution.created_at.isoformat(),
        "updated_at": execution.updated_at.isoformat(),
    }


def log_to_dict(log) -> dict:
    """Convert ExecutionLog to API response dict."""
    return {
        "id": str(log.id),
        "execution_id": str(log.execution_id),
        "step_execution_id": str(log.step_execution_id) if log.step_execution_id else None,
        "level": log.level.value,
        "message": log.message,
        "details": log.details,
        "timestamp": log.timestamp.isoformat(),
    }


# ============================================
# AI ENDPOINTS
# ============================================

@ai_bp.route("/generate-workflow", methods=["POST"])
def generate_workflow():
    """
    Generate workflow steps from a natural language prompt.

    Request body:
    {
        "prompt": "Fetch weather data for London and a cat fact"
    }

    Response: 200 OK with validated steps
    """
    data = request.get_json()

    if not data or not data.get("prompt"):
        return jsonify({"error": "prompt is required"}), 400

    user_prompt = data["prompt"]

    try:
        service = get_workflow_generation_service()
        steps = service.generate_steps(user_prompt)

        return jsonify({
            "success": True,
            "prompt": user_prompt,
            "steps": steps,
        }), 200

    except LLMValidationError as e:
        # The model answered, but not with a usable workflow.
        return jsonify({"error": str(e)}), 400
    except LLMTimeoutError as e:
        return jsonify({"error": str(e), "hint": "Try a simpler prompt"}), 504
    except LLMUnavailableError as e:
        return jsonify({
            "error": str(e),
            "hint": "Make sure Ollama is running: ollama serve",
        }), 503


# ============================================
# ROUTE REGISTRATION
# ============================================

def register_routes(app: Flask) -> None:
    """Register all blueprints with the Flask app."""
    app.register_blueprint(workflows_bp)
    app.register_blueprint(executions_bp)
    app.register_blueprint(ai_bp)
    logger.info("Routes registered (including AI endpoint)")
