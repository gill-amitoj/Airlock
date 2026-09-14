"""
Unit tests for the LLM-backed workflow generation service.

The HTTP call is mocked throughout, so no Ollama server and no network are
needed. DNS is stubbed because generated http_request URLs are validated against
the outbound allowlist.
"""

import json
import pytest
import requests
from unittest.mock import MagicMock, patch

from src.config.settings import Config
from src.services.llm_service import (
    LLMProvider,
    LLMTimeoutError,
    LLMUnavailableError,
    LLMValidationError,
    OllamaProvider,
    WorkflowGenerationService,
)


VALID_STEP = {
    "name": "fetch_joke",
    "task_type": "http_request",
    "description": "Get a random joke",
    "config": {
        "url": "https://official-joke-api.appspot.com/random_joke",
        "method": "GET",
    },
}


class StubProvider(LLMProvider):
    """Provider that returns a canned response, so tests exercise parsing only."""

    def __init__(self, response: str = ""):
        self.response = response
        self.prompts = []

    @property
    def name(self) -> str:
        return "stub"

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


@pytest.fixture(autouse=True)
def stub_dns():
    """Resolve every host to a routable address, with no network access."""
    with patch(
        "src.services.url_guard.resolve_hostname",
        return_value=["93.184.216.34"],
    ) as mock_resolve:
        yield mock_resolve


@pytest.fixture
def config():
    """Default config, so tests do not depend on the caller's environment."""
    return Config()


def make_service(response, config=None, **kwargs):
    """Build a service backed by a stub provider returning `response`."""
    if not isinstance(response, str):
        response = json.dumps(response)
    return WorkflowGenerationService(
        provider=StubProvider(response),
        config=config or Config(),
        **kwargs,
    )


class TestValidResponses:
    """Well-formed model output is accepted."""

    def test_valid_response_parses(self):
        service = make_service([VALID_STEP])

        steps = service.generate_steps("get me a joke")

        assert len(steps) == 1
        assert steps[0]["name"] == "fetch_joke"
        assert steps[0]["config"]["url"].startswith("https://official-joke-api")

    def test_multiple_steps_parse(self):
        second = dict(VALID_STEP, name="fetch_cat")
        second["config"] = {"url": "https://catfact.ninja/fact", "method": "GET"}
        service = make_service([VALID_STEP, second])

        steps = service.generate_steps("joke and a cat fact")

        assert [s["name"] for s in steps] == ["fetch_joke", "fetch_cat"]

    def test_markdown_fence_is_stripped(self):
        """Small models often wrap JSON in a code fence."""
        fenced = f"```json\n{json.dumps([VALID_STEP])}\n```"
        service = make_service(fenced)

        steps = service.generate_steps("get me a joke")

        assert len(steps) == 1

    def test_bare_fence_is_stripped(self):
        fenced = f"```\n{json.dumps([VALID_STEP])}\n```"
        service = make_service(fenced)

        assert len(service.generate_steps("joke")) == 1

    def test_description_is_optional(self):
        step = {k: v for k, v in VALID_STEP.items() if k != "description"}
        service = make_service([step])

        assert len(service.generate_steps("joke")) == 1

    def test_user_prompt_reaches_the_provider(self):
        provider = StubProvider(json.dumps([VALID_STEP]))
        service = WorkflowGenerationService(provider=provider, config=Config())

        service.generate_steps("get me a joke")

        assert "get me a joke" in provider.prompts[0]


class TestMalformedResponses:
    """Anything that is not a clean JSON array of steps is rejected."""

    def test_malformed_json_is_rejected(self):
        service = make_service("this is not json at all")

        with pytest.raises(LLMValidationError, match="did not return valid JSON"):
            service.generate_steps("joke")

    def test_truncated_json_is_rejected(self):
        service = make_service('[{"name": "fetch_joke", "task_ty')

        with pytest.raises(LLMValidationError, match="did not return valid JSON"):
            service.generate_steps("joke")

    def test_prose_wrapping_json_is_rejected(self):
        """
        The old regex scrape would pull a bracketed run out of prose and accept
        it. Strict parsing rejects the whole thing instead.
        """
        service = make_service(
            f"Sure! Here is your workflow: {json.dumps([VALID_STEP])} Hope that helps!"
        )

        with pytest.raises(LLMValidationError, match="did not return valid JSON"):
            service.generate_steps("joke")

    def test_empty_response_is_rejected(self):
        service = make_service("")

        with pytest.raises(LLMValidationError, match="empty response"):
            service.generate_steps("joke")

    def test_object_instead_of_list_is_rejected(self):
        service = make_service(VALID_STEP)

        with pytest.raises(LLMValidationError, match="Expected a list"):
            service.generate_steps("joke")

    def test_empty_list_is_rejected(self):
        service = make_service([])

        with pytest.raises(LLMValidationError, match="no steps"):
            service.generate_steps("joke")

    def test_non_object_step_is_rejected(self):
        service = make_service(["just a string"])

        with pytest.raises(LLMValidationError, match="not an object"):
            service.generate_steps("joke")


class TestStepCap:
    """Generated workflows are capped in size."""

    def test_too_many_steps_is_rejected(self):
        service = make_service([VALID_STEP] * 11)

        with pytest.raises(LLMValidationError, match="exceeds the limit of 10"):
            service.generate_steps("do a hundred things")

    def test_exactly_the_limit_is_allowed(self):
        service = make_service([VALID_STEP] * 10)

        assert len(service.generate_steps("ten things")) == 10

    def test_cap_is_configurable(self):
        config = Config(LLM_MAX_STEPS=2)
        service = make_service([VALID_STEP] * 3, config=config)

        with pytest.raises(LLMValidationError, match="exceeds the limit of 2"):
            service.generate_steps("three things")


class TestStepSchema:
    """Each step must match the expected shape."""

    def test_disallowed_task_type_is_rejected(self):
        step = dict(VALID_STEP, task_type="run_shell_command")
        service = make_service([step])

        with pytest.raises(LLMValidationError, match="unsupported task_type"):
            service.generate_steps("do something")

    def test_missing_task_type_is_rejected(self):
        step = {k: v for k, v in VALID_STEP.items() if k != "task_type"}
        service = make_service([step])

        with pytest.raises(LLMValidationError, match="unsupported task_type"):
            service.generate_steps("joke")

    def test_known_task_types_come_from_the_registry(self):
        """A non-http task type registered in the registry is accepted."""
        step = {"name": "wait", "task_type": "delay", "config": {"seconds": 1}}
        service = make_service([step])

        assert len(service.generate_steps("wait a second")) == 1

    @pytest.mark.parametrize(
        "name",
        ["", "has spaces", "has/slash", "a" * 100, None, 123],
    )
    def test_invalid_names_are_rejected(self, name):
        step = dict(VALID_STEP, name=name)
        service = make_service([step])

        with pytest.raises(LLMValidationError, match="invalid name"):
            service.generate_steps("joke")

    def test_missing_config_is_rejected(self):
        step = {k: v for k, v in VALID_STEP.items() if k != "config"}
        service = make_service([step])

        with pytest.raises(LLMValidationError, match="missing a config"):
            service.generate_steps("joke")

    def test_non_text_description_is_rejected(self):
        step = dict(VALID_STEP, description={"nested": "object"})
        service = make_service([step])

        with pytest.raises(LLMValidationError, match="non-text description"):
            service.generate_steps("joke")

    def test_one_bad_step_rejects_the_whole_response(self):
        """No partial acceptance."""
        bad = dict(VALID_STEP, task_type="run_shell_command")
        service = make_service([VALID_STEP, bad, VALID_STEP])

        with pytest.raises(LLMValidationError):
            service.generate_steps("three things")


class TestGeneratedUrlValidation:
    """Generated URLs are checked against the allowlist at generation time."""

    def test_non_allowlisted_url_is_rejected(self):
        step = dict(VALID_STEP)
        step["config"] = {"url": "https://evil.example.com/exfil", "method": "GET"}
        service = make_service([step])

        with pytest.raises(LLMValidationError, match="not permitted"):
            service.generate_steps("send my data somewhere")

    def test_private_address_url_is_rejected(self, stub_dns):
        stub_dns.return_value = ["169.254.169.254"]
        service = make_service([VALID_STEP])

        with pytest.raises(LLMValidationError, match="not permitted"):
            service.generate_steps("read cloud metadata")

    def test_missing_url_is_rejected(self):
        step = dict(VALID_STEP, config={"method": "GET"})
        service = make_service([step])

        with pytest.raises(LLMValidationError, match="missing a URL"):
            service.generate_steps("joke")

    def test_invalid_method_is_rejected(self):
        step = dict(VALID_STEP)
        step["config"] = {
            "url": "https://catfact.ninja/fact",
            "method": "CONNECT",
        }
        service = make_service([step])

        with pytest.raises(LLMValidationError, match="invalid method"):
            service.generate_steps("joke")

    def test_allowlist_can_be_injected(self):
        step = dict(VALID_STEP)
        step["config"] = {"url": "https://internal.test/data", "method": "GET"}
        service = make_service([step], allowed_hosts=["internal.test"])

        assert len(service.generate_steps("fetch internal data")) == 1


class TestPromptValidation:
    """The user's prompt is checked before the model is called."""

    @pytest.mark.parametrize("prompt", ["", "   ", None])
    def test_empty_prompt_is_rejected(self, prompt):
        service = make_service([VALID_STEP])

        with pytest.raises(LLMValidationError, match="prompt is required"):
            service.generate_steps(prompt)

    def test_overlong_prompt_is_rejected(self):
        service = make_service([VALID_STEP])

        with pytest.raises(LLMValidationError, match="too long"):
            service.generate_steps("x" * 1001)

    def test_provider_is_not_called_for_invalid_prompt(self):
        provider = StubProvider(json.dumps([VALID_STEP]))
        service = WorkflowGenerationService(provider=provider, config=Config())

        with pytest.raises(LLMValidationError):
            service.generate_steps("")

        assert provider.prompts == []


class TestOllamaProvider:
    """Transport behaviour, with requests.post mocked."""

    @patch("requests.post")
    def test_successful_generation(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            **{"json.return_value": {"response": "generated text"}},
        )
        provider = OllamaProvider(Config())

        assert provider.generate("hello") == "generated text"

    @patch("requests.post")
    def test_payload_uses_config(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            **{"json.return_value": {"response": "[]"}},
        )
        config = Config(
            OLLAMA_MODEL="test-model",
            LLM_TEMPERATURE=0.9,
            LLM_NUM_PREDICT=123,
            LLM_TIMEOUT=7,
        )

        OllamaProvider(config).generate("hello")

        kwargs = mock_post.call_args.kwargs
        assert kwargs["json"]["model"] == "test-model"
        assert kwargs["json"]["options"]["temperature"] == 0.9
        assert kwargs["json"]["options"]["num_predict"] == 123
        assert kwargs["timeout"] == 7

    @patch("requests.post", side_effect=requests.exceptions.Timeout())
    def test_timeout_raises_typed_error(self, mock_post):
        with pytest.raises(LLMTimeoutError, match="did not respond in time"):
            OllamaProvider(Config()).generate("hello")

    @patch("requests.post", side_effect=requests.exceptions.ConnectionError())
    def test_connection_error_raises_typed_error(self, mock_post):
        with pytest.raises(LLMUnavailableError, match="Could not connect"):
            OllamaProvider(Config()).generate("hello")

    @patch("requests.post")
    def test_non_200_raises_typed_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text="boom")

        with pytest.raises(LLMUnavailableError, match="HTTP 500"):
            OllamaProvider(Config()).generate("hello")

    @patch("requests.post")
    def test_non_json_envelope_raises_typed_error(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            **{"json.side_effect": ValueError("not json")},
        )

        with pytest.raises(LLMUnavailableError, match="non-JSON"):
            OllamaProvider(Config()).generate("hello")

    @patch("requests.post")
    def test_name_includes_model(self, mock_post):
        assert OllamaProvider(Config(OLLAMA_MODEL="m1")).name == "ollama:m1"
