"""
Workflow orchestrator - the core execution engine.

Coordinates workflow execution, manages step execution,
handles failures and retries.
"""

import logging
import time
import traceback
from typing import Any, Dict, Optional
from uuid import UUID

from src.domain import ExecutionStatus, StepStatus, LogLevel
from src.domain.entities import StepExecution, WorkflowStep
from src.persistence import WorkflowRepository, ExecutionRepository, LogRepository
from src.config import get_config
from .execution_service import ExecutionService
from .redaction import redact_details
from .task_handlers import TaskHandlerRegistry, TaskHandler

logger = logging.getLogger(__name__)


class OrchestratorError(Exception):
    """Base exception for orchestrator errors."""
    pass


class StepExecutionError(OrchestratorError):
    """Raised when a step fails to execute."""
    
    def __init__(self, step_name: str, message: str, details: Optional[Dict] = None):
        self.step_name = step_name
        self.details = details or {}
        super().__init__(f"Step '{step_name}' failed: {message}")


class WorkflowOrchestrator:
    """
    Core workflow execution engine.
    
    Responsibilities:
    - Execute workflow steps in order
    - Handle step failures and retries
    - Manage execution state persistence
    - Support resumable executions
    """
    
    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        execution_repo: ExecutionRepository,
        log_repo: LogRepository,
        task_registry: Optional[TaskHandlerRegistry] = None,
    ):
        self.workflow_repo = workflow_repo
        self.execution_repo = execution_repo
        self.log_repo = log_repo
        self.execution_service = ExecutionService(
            execution_repo, workflow_repo, log_repo
        )
        self.task_registry = task_registry or TaskHandlerRegistry()
        self.config = get_config()
    
    def execute(self, execution_id: UUID) -> Dict[str, Any]:
        """
        Execute a workflow from its current state.
        
        This is the main entry point for workflow execution.
        Supports resuming from the last successful step.
        """
        execution = self.execution_service.get_execution(execution_id)
        workflow = self.workflow_repo.get_workflow_by_id(execution.workflow_id)
        
        if not workflow:
            raise OrchestratorError(f"Workflow {execution.workflow_id} not found")
        
        # Validate execution can be started
        if execution.status == ExecutionStatus.COMPLETED:
            logger.info(f"Execution {execution_id} already completed")
            return {"status": "already_completed", "output": execution.output_data}
        
        if execution.status == ExecutionStatus.CANCELLED:
            raise OrchestratorError(f"Execution {execution_id} was cancelled")
        
        # Transition to RUNNING if coming from PENDING or RETRYING
        if execution.status in (ExecutionStatus.PENDING, ExecutionStatus.RETRYING):
            execution = self.execution_service.start_execution(execution_id)
        
        logger.info(
            f"Starting execution {execution_id} from step {execution.current_step_order}"
        )
        
        try:
            # Get remaining steps to execute
            steps = [s for s in workflow.steps if s.step_order >= execution.current_step_order]
            steps.sort(key=lambda s: s.step_order)
            
            # Execute each step
            step_outputs: Dict[str, Any] = {}
            current_data = execution.input_data.copy()
            
            for step in steps:
                logger.info(f"Executing step {step.step_order}: {step.name}")
                
                try:
                    output = self._execute_step(
                        execution_id=execution_id,
                        step=step,
                        input_data=current_data,
                    )
                    
                    step_outputs[step.name] = output

                    # Namespace each step's output under its own name before
                    # passing it on. A flat merge would clobber earlier steps -
                    # every http_request step returns the same two keys
                    # (status_code, response) - so later steps would only ever
                    # see the most recent one. Nesting keeps every prior step
                    # reachable, addressable in templates as {step_name[key]}.
                    if output:
                        current_data[step.name] = output

                    # Update execution progress
                    self.execution_repo.update_execution_status(
                        execution_id,
                        ExecutionStatus.RUNNING,
                        current_step_order=step.step_order + 1,
                    )
                    
                except StepExecutionError as e:
                    logger.error(f"Step {step.name} failed: {e}")
                    self._handle_step_failure(execution_id, step, e)
                    raise
            
            # All steps completed successfully
            final_output = {
                "steps": step_outputs,
                "final_data": current_data,
            }
            
            self.execution_service.complete_execution(execution_id, final_output)
            logger.info(f"Execution {execution_id} completed successfully")
            
            return {"status": "completed", "output": final_output}
            
        except StepExecutionError:
            # Already handled in step execution
            return {"status": "failed", "execution_id": str(execution_id)}
        except Exception as e:
            # Unexpected error
            logger.exception(f"Unexpected error in execution {execution_id}")
            self.execution_service.fail_execution(
                execution_id,
                f"Unexpected error: {str(e)}",
            )
            raise OrchestratorError(f"Execution failed: {e}") from e
    
    def _execute_step(
        self,
        execution_id: UUID,
        step: WorkflowStep,
        input_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Execute a single workflow step with retry logic.
        
        Returns the step output on success, raises StepExecutionError on failure.
        """
        # Create step execution record
        step_exec = self.execution_service.create_step_execution(
            execution_id=execution_id,
            step_id=step.id,
            step_order=step.step_order,
            input_data=input_data,
        )
        
        # Get the appropriate task handler
        handler = self.task_registry.get_handler(step.task_type)
        if not handler:
            error_msg = f"No handler registered for task type: {step.task_type}"
            self._fail_step(step_exec.id, execution_id, error_msg)
            raise StepExecutionError(step.name, error_msg)
        
        # Execute with retries
        attempt = 0
        last_error = None
        
        while attempt < step.max_retries:
            attempt += 1
            
            try:
                # Mark step as running
                self.execution_service.update_step_execution(
                    step_exec.id, StepStatus.RUNNING
                )
                
                self._log(
                    execution_id,
                    LogLevel.INFO,
                    f"Starting step '{step.name}' (attempt {attempt}/{step.max_retries})",
                    step_execution_id=step_exec.id,
                    attempt=attempt,
                )
                
                # Execute the handler
                output = handler.execute(
                    step_config=step.config,
                    input_data=input_data,
                    timeout=step.timeout_seconds,
                )
                
                # Success!
                self.execution_service.update_step_execution(
                    step_exec.id,
                    StepStatus.COMPLETED,
                    output_data=output,
                )
                
                self._log(
                    execution_id,
                    LogLevel.INFO,
                    f"Step '{step.name}' completed successfully",
                    step_execution_id=step_exec.id,
                )
                
                return output
                
            except Exception as e:
                last_error = e
                error_details = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": traceback.format_exc(),
                    "attempt": attempt,
                }
                
                self._log(
                    execution_id,
                    LogLevel.ERROR,
                    f"Step '{step.name}' attempt {attempt} failed: {e}",
                    step_execution_id=step_exec.id,
                    **error_details,
                )
                
                if attempt < step.max_retries:
                    # Calculate exponential backoff delay
                    delay = self._calculate_backoff(attempt)
                    logger.info(f"Retrying step '{step.name}' in {delay:.2f}s")
                    time.sleep(delay)
        
        # All retries exhausted
        error_msg = f"Step failed after {step.max_retries} attempts: {last_error}"
        self._fail_step(
            step_exec.id,
            execution_id,
            str(last_error),
            error_details={"final_attempt": attempt, "error": str(last_error)},
        )
        
        raise StepExecutionError(step.name, error_msg, {"last_error": str(last_error)})
    
    def _fail_step(
        self,
        step_exec_id: UUID,
        execution_id: UUID,
        error_message: str,
        error_details: Optional[Dict] = None,
    ) -> None:
        """Mark a step as failed."""
        self.execution_service.update_step_execution(
            step_exec_id,
            StepStatus.FAILED,
            error_message=error_message,
            error_details=error_details,
        )
    
    def _handle_step_failure(
        self,
        execution_id: UUID,
        step: WorkflowStep,
        error: StepExecutionError,
    ) -> None:
        """Handle step failure - update execution status."""
        self.execution_service.fail_execution(
            execution_id,
            f"Step '{step.name}' failed: {error}",
        )
    
    def _calculate_backoff(self, attempt: int) -> float:
        """
        Calculate exponential backoff delay.
        
        Formula: min(base_delay * 2^attempt, max_delay)
        """
        delay = self.config.RETRY_BASE_DELAY * (2 ** attempt)
        return min(delay, self.config.RETRY_MAX_DELAY)
    
    def _log(
        self,
        execution_id: UUID,
        level: LogLevel,
        message: str,
        step_execution_id: Optional[UUID] = None,
        **details,
    ) -> None:
        """
        Create an execution log entry.

        Details are redacted first - step failures log error details that can
        quote step config, and step config can carry auth headers.
        """
        from src.domain import ExecutionLog

        log = ExecutionLog.create(
            execution_id=execution_id,
            level=level,
            message=message,
            step_execution_id=step_execution_id,
            details=redact_details(details),
        )
        self.log_repo.create_log(log)
