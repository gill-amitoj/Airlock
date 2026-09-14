"""
LLM-backed workflow generation.

Turns a natural-language request into workflow steps. The model is treated as an
untrusted source throughout: it proposes steps, and this module decides whether
those steps are acceptable.

Layering mirrors the rest of the service package - the API route is a thin
controller and all generation logic lives here. The provider interface exists so
a different backend can be substituted without touching workflow code.

Validation is deliberately strict. A small model will occasionally emit prose,
truncated JSON, invented task types, or a hundred steps; all of those are
rejected outright rather than partially accepted.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional

import requests

from src.config import Config, get_config
from .task_handlers import TaskHandlerRegistry, create_default_registry
from .url_guard import BlockedUrlError, validate_url

logger = logging.getLogger(__name__)


# ============================================
# ERRORS
# ============================================

class LLMError(Exception):
    """Base class for LLM failures."""


class LLMUnavailableError(LLMError):
    """The model backend could not be reached."""


class LLMTimeoutError(LLMError):
    """The model did not respond in time."""


class LLMValidationError(LLMError):
    """The model responded, but the response was not a usable workflow."""


# ============================================
# PROVIDER INTERFACE
# ============================================

class LLMProvider(ABC):
    """
    Interface for a text-generation backend.

    Kept to a single method so swapping backends is a matter of writing one
    class. Implementations raise LLMUnavailableError / LLMTimeoutError rather
    than leaking transport-specific exceptions to callers.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for the backend, used in logs."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Generate a completion for `prompt` and return the raw text."""


class OllamaProvider(LLMProvider):
    """Talks to a local Ollama server's /api/generate endpoint."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()

    @property
    def name(self) -> str:
        return f"ollama:{self.config.OLLAMA_MODEL}"

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.config.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.config.LLM_TEMPERATURE,
                "num_predict": self.config.LLM_NUM_PREDICT,
            },
        }

        # Note: the Ollama endpoint is operator-configured and typically on a
        # private address (host.docker.internal), so it deliberately does not go
        # through url_guard - that guard is for model- and user-supplied URLs.
        try:
            response = requests.post(
                self.config.OLLAMA_URL,
                json=payload,
                timeout=self.config.LLM_TIMEOUT,
            )
        except requests.exceptions.Timeout as e:
            raise LLMTimeoutError("The AI model did not respond in time") from e
        except requests.exceptions.ConnectionError as e:
            raise LLMUnavailableError("Could not connect to the AI model") from e
        except requests.exceptions.RequestException as e:
            raise LLMUnavailableError(f"AI request failed: {e}") from e

        if response.status_code != 200:
            logger.error(f"Ollama returned {response.status_code}")
            raise LLMUnavailableError(
                f"AI model returned an error (HTTP {response.status_code})"
            )

        try:
            body = response.json()
        except ValueError as e:
            raise LLMUnavailableError("AI model returned a non-JSON envelope") from e

        return body.get("response", "")


# ============================================
# WORKFLOW GENERATION
# ============================================

SYSTEM_PROMPT = """You are a workflow generator. Given a user request, generate workflow steps as a JSON array.
Each step must have:
- "name": a short snake_case name
- "task_type": always "http_request"
- "description": what this step does
- "config": object with "url" (a real public API URL) and "method" (GET or POST)

Use only these free public APIs:
- Jokes: https://official-joke-api.appspot.com/random_joke
- Cat facts: https://catfact.ninja/fact
- Random users: https://randomuser.me/api/
- Quotes: https://api.quotable.io/random
- Dog pics: https://dog.ceo/api/breeds/image/random
- Activities: https://www.boredapi.com/api/activity
- Weather: https://wttr.in/London?format=j1
- News: https://hacker-news.firebaseio.com/v0/topstories.json
- Posts: https://jsonplaceholder.typicode.com/posts/1
- Todos: https://jsonplaceholder.typicode.com/todos/1
- Users: https://jsonplaceholder.typicode.com/users/1

Respond ONLY with a valid JSON array, no explanation. Example:
[{"name":"fetch_joke","task_type":"http_request","description":"Get a random joke","config":{"url":"https://official-joke-api.appspot.com/random_joke","method":"GET"}}]"""

# Small models routinely wrap JSON in a markdown fence. Stripping a fence is a
# formatting fix, not a parsing guess - the contents must still be valid JSON.
_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

# Step names become workflow step names, so keep them to a conservative shape.
_STEP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")

MAX_PROMPT_LENGTH = 1000


class WorkflowGenerationService:
    """
    Generates validated workflow steps from a natural-language prompt.

    Owns the system prompt, the provider call, and validation of what comes back.
    """

    def __init__(
        self,
        provider: Optional[LLMProvider] = None,
        config: Optional[Config] = None,
        registry: Optional[TaskHandlerRegistry] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
    ):
        self.config = config or get_config()
        self.provider = provider or OllamaProvider(self.config)
        # Task types are read from the handler registry rather than a second
        # hardcoded list, so the two cannot drift apart.
        self.registry = registry or create_default_registry()
        self._allowed_hosts = allowed_hosts

    @property
    def allowed_hosts(self) -> Iterable[str]:
        if self._allowed_hosts is None:
            return self.config.ALLOWED_HTTP_HOSTS
        return self._allowed_hosts

    def generate_steps(self, user_prompt: str) -> List[Dict[str, Any]]:
        """
        Generate workflow steps for a user request.

        Returns:
            A list of validated step dicts.

        Raises:
            LLMValidationError: The prompt was unusable, or the response was not
                a valid workflow.
            LLMUnavailableError / LLMTimeoutError: The backend could not answer.
        """
        if not user_prompt or not user_prompt.strip():
            raise LLMValidationError("A prompt is required")

        if len(user_prompt) > MAX_PROMPT_LENGTH:
            raise LLMValidationError(
                f"Prompt is too long (max {MAX_PROMPT_LENGTH} characters)"
            )

        full_prompt = f"{SYSTEM_PROMPT}\n\nUser request: {user_prompt.strip()}"

        logger.info(f"Generating workflow via {self.provider.name}")
        raw_response = self.provider.generate(full_prompt)

        steps = self._parse(raw_response)
        self._validate(steps)
        return steps

    def _parse(self, raw_response: str) -> Any:
        """
        Parse the model's raw text into JSON.

        Replaces the previous regex scrape of the first [...] run, which would
        happily accept a bracketed fragment out of the middle of a sentence.
        """
        if not raw_response or not raw_response.strip():
            raise LLMValidationError("The AI returned an empty response")

        candidate = raw_response.strip()

        fence_match = _FENCE_PATTERN.match(candidate)
        if fence_match:
            candidate = fence_match.group(1).strip()

        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            logger.warning(f"AI returned unparseable JSON: {candidate[:200]}")
            raise LLMValidationError(
                "The AI did not return valid JSON. Try a simpler request."
            ) from e

    def _validate(self, steps: Any) -> None:
        """
        Validate the parsed response against the step schema.

        Any single invalid step rejects the whole response - a partially valid
        workflow is not something to silently half-accept.
        """
        if not isinstance(steps, list):
            raise LLMValidationError(
                f"Expected a list of steps, got {type(steps).__name__}"
            )

        if not steps:
            raise LLMValidationError("The AI returned no steps")

        max_steps = self.config.LLM_MAX_STEPS
        if len(steps) > max_steps:
            raise LLMValidationError(
                f"Generated workflow has {len(steps)} steps, which exceeds the "
                f"limit of {max_steps}"
            )

        known_task_types = set(self.registry.list_task_types())

        for index, step in enumerate(steps):
            self._validate_step(step, index, known_task_types)

    def _validate_step(
        self,
        step: Any,
        index: int,
        known_task_types: set,
    ) -> None:
        """Validate one generated step."""
        position = f"step {index + 1}"

        if not isinstance(step, dict):
            raise LLMValidationError(f"{position} is not an object")

        name = step.get("name")
        if not isinstance(name, str) or not _STEP_NAME_PATTERN.match(name):
            raise LLMValidationError(
                f"{position} has an invalid name: {name!r}"
            )

        task_type = step.get("task_type")
        if task_type not in known_task_types:
            raise LLMValidationError(
                f"{position} has an unsupported task_type: {task_type!r} "
                f"(supported: {', '.join(sorted(known_task_types))})"
            )

        config = step.get("config")
        if not isinstance(config, dict):
            raise LLMValidationError(
                f"{position} is missing a config object"
            )

        description = step.get("description")
        if description is not None and not isinstance(description, str):
            raise LLMValidationError(f"{position} has a non-text description")

        if task_type == "http_request":
            self._validate_http_step(config, position)

    def _validate_http_step(self, config: Dict[str, Any], position: str) -> None:
        """
        Validate an http_request step's URL.

        The handler enforces this again at execution time; checking here as well
        means an unusable workflow is rejected before it is ever created, and the
        user gets a clear message instead of a step failure later.
        """
        url = config.get("url")
        if not isinstance(url, str) or not url:
            raise LLMValidationError(f"{position} is missing a URL")

        method = config.get("method", "GET")
        if not isinstance(method, str) or method.upper() not in (
            "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD",
        ):
            raise LLMValidationError(f"{position} has an invalid method: {method!r}")

        try:
            validate_url(url, self.allowed_hosts)
        except BlockedUrlError as e:
            raise LLMValidationError(
                f"{position} points at a URL that is not permitted: {e.reason}"
            ) from e
