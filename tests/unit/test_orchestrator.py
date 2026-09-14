"""
Unit tests for the workflow orchestrator.

Focused on how step output is threaded between steps. Repositories are mocked,
so these tests need no database.
"""

import pytest
from unittest.mock import MagicMock
from uuid import uuid4

from src.domain import ExecutionStatus, Workflow, WorkflowStep, WorkflowExecution
from src.domain.entities import StepExecution
from src.services.orchestrator import WorkflowOrchestrator
from src.services.task_handlers import TaskHandler, TaskHandlerRegistry


class RecordingHandler(TaskHandler):
    """
    Test handler that returns the same keys on every call.

    Two steps both returning status_code/response is exactly the case a flat
    merge would collapse, so it is what these tests use. It records the
    input_data it was handed so the chaining can be asserted.
    """

    def __init__(self):
        self.seen_inputs = []
        self.call_count = 0

    @property
    def task_type(self) -> str:
        return "http_request"

    def execute(self, step_config, input_data, timeout=300):
        self.seen_inputs.append(dict(input_data))
        self.call_count += 1
        return {
            "status_code": 200,
            "response": {"body": step_config.get("marker")},
        }


@pytest.fixture
def handler():
    return RecordingHandler()


@pytest.fixture
def registry(handler):
    reg = TaskHandlerRegistry()
    reg.register(handler)
    return reg


@pytest.fixture
def workflow():
    """A two-step workflow whose steps both use the recording handler."""
    workflow_id = uuid4()
    wf = Workflow.create(name="two-step", description="")
    wf.id = workflow_id
    wf.steps = [
        WorkflowStep.create(
            workflow_id=workflow_id,
            name="fetch_joke",
            task_type="http_request",
            step_order=0,
            config={"marker": "joke"},
        ),
        WorkflowStep.create(
            workflow_id=workflow_id,
            name="fetch_user",
            task_type="http_request",
            step_order=1,
            config={"marker": "user"},
        ),
    ]
    return wf


@pytest.fixture
def execution(workflow):
    return WorkflowExecution.create(
        workflow_id=workflow.id,
        idempotency_key="test-key",
        input_data={"seed": "original"},
    )


@pytest.fixture
def orchestrator(workflow, execution, registry):
    workflow_repo = MagicMock()
    execution_repo = MagicMock()
    log_repo = MagicMock()

    workflow_repo.get_workflow_by_id.return_value = workflow
    # The same execution object is returned throughout, so status mutations made
    # by ExecutionService persist across calls the way they would in the DB.
    execution_repo.get_execution_by_id.return_value = execution
    execution_repo.create_step_execution.side_effect = lambda step_exec: step_exec

    return WorkflowOrchestrator(
        workflow_repo=workflow_repo,
        execution_repo=execution_repo,
        log_repo=log_repo,
        task_registry=registry,
    )


class TestStepOutputNamespacing:
    """Each step's output must be namespaced, not merged flat."""

    def test_outputs_are_namespaced_by_step_name(self, orchestrator, execution):
        """Both steps' outputs survive under their own names."""
        result = orchestrator.execute(execution.id)

        assert result["status"] == "completed"
        final_data = result["output"]["final_data"]

        assert final_data["fetch_joke"]["response"]["body"] == "joke"
        assert final_data["fetch_user"]["response"]["body"] == "user"

    def test_second_step_receives_first_step_data(self, orchestrator, execution, handler):
        """The second step's input carries the first step's namespaced output."""
        orchestrator.execute(execution.id)

        assert handler.call_count == 2
        first_input, second_input = handler.seen_inputs

        # Step 1 sees only the execution input.
        assert first_input == {"seed": "original"}

        # Step 2 sees the execution input plus step 1, nested under its name.
        assert second_input["seed"] == "original"
        assert second_input["fetch_joke"]["response"]["body"] == "joke"

    def test_identical_keys_do_not_collide(self, orchestrator, execution, handler):
        """
        The regression this guards: both steps return status_code/response, and
        a flat merge left only the last step's values reachable.
        """
        result = orchestrator.execute(execution.id)
        final_data = result["output"]["final_data"]

        # Neither step's keys leak to the top level.
        assert "status_code" not in final_data
        assert "response" not in final_data

        # Both are reachable, and they are distinct.
        assert final_data["fetch_joke"] != final_data["fetch_user"]

    def test_original_input_data_is_preserved(self, orchestrator, execution):
        """Execution input survives alongside the step namespaces."""
        result = orchestrator.execute(execution.id)

        assert result["output"]["final_data"]["seed"] == "original"

    def test_steps_envelope_still_populated(self, orchestrator, execution):
        """The existing output['steps'] envelope is unchanged."""
        result = orchestrator.execute(execution.id)

        steps = result["output"]["steps"]
        assert set(steps.keys()) == {"fetch_joke", "fetch_user"}
        assert steps["fetch_joke"]["response"]["body"] == "joke"
