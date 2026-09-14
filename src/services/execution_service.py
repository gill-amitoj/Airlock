"""
Execution service for managing workflow executions.

Handles creation, status management, and querying of executions.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from src.domain import (
    WorkflowExecution, ExecutionLog, ExecutionStatus, LogLevel, WorkflowStatus
)
from src.domain.entities import StepExecution
from src.domain.state_machine import WorkflowStateMachine, InvalidTransitionError
from src.persistence import ExecutionRepository, LogRepository, WorkflowRepository
from .redaction import redact_details

logger = logging.getLogger(__name__)


class ExecutionServiceError(Exception):
    """Base exception for execution service errors."""
    pass


class ExecutionNotFoundError(ExecutionServiceError):
    """Raised when an execution is not found."""
    pass


class DuplicateExecutionError(ExecutionServiceError):
    """Raised when a duplicate execution is attempted."""
    
    def __init__(self, existing_execution: WorkflowExecution):
        self.existing_execution = existing_execution
        super().__init__(
            f"Execution already exists with idempotency key: {existing_execution.idempotency_key}"
        )


class ExecutionStateError(ExecutionServiceError):
    """Raised when an invalid state transition is attempted."""
    pass


class ExecutionService:
    """
    Service for managing workflow executions.
    
    Handles execution lifecycle, state transitions, and logging.
    """
    
    def __init__(
        self,
        execution_repo: ExecutionRepository,
        workflow_repo: WorkflowRepository,
        log_repo: LogRepository,
    ):
        self.execution_repo = execution_repo
        self.workflow_repo = workflow_repo
        self.log_repo = log_repo
    
    def create_execution(
        self,
        workflow_id: UUID,
        idempotency_key: str,
        input_data: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
        scheduled_at: Optional[datetime] = None,
    ) -> WorkflowExecution:
        """
        Create a new workflow execution.
        
        Uses idempotency_key to prevent duplicate executions.
        Returns existing execution if one exists with the same key.
        """
        # Validate workflow exists and is active
        workflow = self.workflow_repo.get_workflow_by_id(workflow_id)
        if not workflow:
            raise ExecutionServiceError(f"Workflow {workflow_id} not found")
        
        if workflow.status != WorkflowStatus.ACTIVE:
            raise ExecutionServiceError(
                f"Cannot execute workflow in {workflow.status.value} status"
            )
        
        # Check for existing execution with same idempotency key
        existing = self.execution_repo.get_execution_by_idempotency_key(
            workflow_id, idempotency_key
        )
        if existing:
            logger.info(
                f"Returning existing execution {existing.id} for idempotency key {idempotency_key}"
            )
            raise DuplicateExecutionError(existing)
        
        # Create new execution
        execution = WorkflowExecution.create(
            workflow_id=workflow_id,
            idempotency_key=idempotency_key,
            input_data=input_data,
            max_retries=max_retries,
            scheduled_at=scheduled_at,
        )
        
        execution = self.execution_repo.create_execution(execution)
        
        # Log creation
        self._log(
            execution.id,
            LogLevel.INFO,
            f"Execution created for workflow {workflow_id}",
            input_data=input_data,
            idempotency_key=idempotency_key,
        )
        
        logger.info(f"Created execution {execution.id} for workflow {workflow_id}")
        return execution
    
    def get_execution(self, execution_id: UUID) -> WorkflowExecution:
        """Get an execution by ID."""
        execution = self.execution_repo.get_execution_by_id(execution_id)
        
        if not execution:
            raise ExecutionNotFoundError(f"Execution {execution_id} not found")
        
        return execution
    
    def transition_status(
        self,
        execution_id: UUID,
        new_status: ExecutionStatus,
        error_message: Optional[str] = None,
        current_step_order: Optional[int] = None,
    ) -> WorkflowExecution:
        """
        Transition execution to a new status.
        
        Validates the transition using the state machine.
        """
        execution = self.get_execution(execution_id)
        
        try:
            WorkflowStateMachine.validate_transition(execution.status, new_status)
        except InvalidTransitionError as e:
            raise ExecutionStateError(str(e))
        
        self.execution_repo.update_execution_status(
            execution_id,
            new_status,
            error_message=error_message,
            current_step_order=current_step_order,
        )
        
        self._log(
            execution_id,
            LogLevel.INFO,
            f"Status changed: {execution.status.value} → {new_status.value}",
            previous_status=execution.status.value,
            new_status=new_status.value,
            error_message=error_message,
        )
        
        execution.status = new_status
        if error_message:
            execution.error_message = error_message
        if current_step_order is not None:
            execution.current_step_order = current_step_order
        
        return execution
    
    def start_execution(self, execution_id: UUID) -> WorkflowExecution:
        """Mark an execution as running."""
        return self.transition_status(execution_id, ExecutionStatus.RUNNING)
    
    def complete_execution(
        self,
        execution_id: UUID,
        output_data: Optional[Dict[str, Any]] = None,
    ) -> WorkflowExecution:
        """Mark an execution as completed."""
        execution = self.transition_status(execution_id, ExecutionStatus.COMPLETED)
        
        if output_data:
            self.execution_repo.set_output_data(execution_id, output_data)
            execution.output_data = output_data
        
        return execution
    
    def fail_execution(
        self,
        execution_id: UUID,
        error_message: str,
    ) -> WorkflowExecution:
        """Mark an execution as failed."""
        return self.transition_status(
            execution_id,
            ExecutionStatus.FAILED,
            error_message=error_message,
        )
    
    def retry_execution(self, execution_id: UUID) -> WorkflowExecution:
        """
        Initiate retry for a failed execution.
        
        Increments retry count and transitions to RETRYING status.
        """
        execution = self.get_execution(execution_id)
        
        if execution.status != ExecutionStatus.FAILED:
            raise ExecutionStateError(
                f"Can only retry failed executions, current status: {execution.status.value}"
            )
        
        if execution.retry_count >= execution.max_retries:
            raise ExecutionStateError(
                f"Maximum retries ({execution.max_retries}) exceeded"
            )
        
        # Increment retry count
        new_retry_count = self.execution_repo.increment_retry_count(execution_id)
        
        # Transition to RETRYING
        execution = self.transition_status(execution_id, ExecutionStatus.RETRYING)
        execution.retry_count = new_retry_count
        
        self._log(
            execution_id,
            LogLevel.INFO,
            f"Retry initiated (attempt {new_retry_count} of {execution.max_retries})",
            retry_count=new_retry_count,
            max_retries=execution.max_retries,
        )
        
        return execution
    
    def cancel_execution(self, execution_id: UUID) -> WorkflowExecution:
        """Cancel an execution."""
        execution = self.get_execution(execution_id)
        
        if execution.is_terminal:
            raise ExecutionStateError(
                f"Cannot cancel execution in terminal status: {execution.status.value}"
            )
        
        return self.transition_status(execution_id, ExecutionStatus.CANCELLED)
    
    def create_step_execution(
        self,
        execution_id: UUID,
        step_id: UUID,
        step_order: int,
        input_data: Optional[Dict[str, Any]] = None,
    ) -> StepExecution:
        """Create a new step execution record."""
        step_exec = StepExecution.create(
            execution_id=execution_id,
            step_id=step_id,
            step_order=step_order,
            input_data=input_data,
        )
        
        return self.execution_repo.create_step_execution(step_exec)
    
    def update_step_execution(
        self,
        step_exec_id: UUID,
        status,
        output_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        error_details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update a step execution."""
        return self.execution_repo.update_step_execution(
            step_exec_id,
            status,
            output_data=output_data,
            error_message=error_message,
            error_details=error_details,
        )
    
    def list_executions(
        self,
        workflow_id: Optional[UUID] = None,
        status: Optional[ExecutionStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[WorkflowExecution]:
        """List executions with optional filters."""
        return self.execution_repo.list_executions(
            workflow_id=workflow_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    
    def get_execution_logs(
        self,
        execution_id: UUID,
        level: Optional[LogLevel] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[ExecutionLog]:
        """Get logs for an execution."""
        # Verify execution exists
        self.get_execution(execution_id)
        
        return self.log_repo.get_logs_by_execution_id(
            execution_id,
            level=level,
            limit=limit,
            offset=offset,
        )
    
    def _log(
        self,
        execution_id: UUID,
        level: LogLevel,
        message: str,
        step_execution_id: Optional[UUID] = None,
        **details,
    ) -> ExecutionLog:
        """
        Create an execution log entry.

        Details are redacted first - create_execution logs caller-supplied
        input_data, which is a realistic place for a credential to appear.
        """
        log = ExecutionLog.create(
            execution_id=execution_id,
            level=level,
            message=message,
            step_execution_id=step_execution_id,
            details=redact_details(details),
        )
        return self.log_repo.create_log(log)
