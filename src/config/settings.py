"""
Application configuration management.

Supports environment-based configuration with sensible defaults.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Tuple


# Hostnames behind the public APIs the AI workflow generator is allowed to suggest.
# The generator prompt lists 11 URLs, but several share a host, so this is 9 entries.
DEFAULT_ALLOWED_HTTP_HOSTS: Tuple[str, ...] = (
    "official-joke-api.appspot.com",
    "catfact.ninja",
    "randomuser.me",
    "api.quotable.io",
    "dog.ceo",
    "www.boredapi.com",
    "wttr.in",
    "hacker-news.firebaseio.com",
    "jsonplaceholder.typicode.com",
)


def _parse_host_list(raw: Optional[str]) -> Tuple[str, ...]:
    """Parse a comma-separated host list, ignoring blanks and surrounding space."""
    if raw is None:
        return DEFAULT_ALLOWED_HTTP_HOSTS
    hosts = tuple(h.strip().lower() for h in raw.split(",") if h.strip())
    return hosts


@dataclass
class Config:
    """Application configuration container."""
    
    # Flask settings
    FLASK_ENV: str = "development"
    FLASK_DEBUG: bool = True
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    
    # Database settings
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/workflow_engine"
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    
    # Redis settings
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 10
    
    # Worker settings
    WORKER_CONCURRENCY: int = 4
    MAX_RETRIES: int = 3
    RETRY_BASE_DELAY: float = 1.0  # Base delay in seconds for exponential backoff
    RETRY_MAX_DELAY: float = 300.0  # Maximum delay in seconds
    TASK_TIMEOUT: int = 3600  # Default task timeout in seconds (1 hour)
    
    # Queue settings
    QUEUE_NAME: str = "workflow_tasks"
    QUEUE_PROCESSING_TIMEOUT: int = 30  # Visibility timeout in seconds

    # Outbound HTTP settings
    # Hostnames that http_request steps are permitted to call. Step URLs come from
    # users and from the AI workflow generator, so this allowlist - not the model
    # prompt - is what actually constrains where the engine sends requests.
    ALLOWED_HTTP_HOSTS: tuple = DEFAULT_ALLOWED_HTTP_HOSTS

    # LLM settings (AI workflow generator)
    OLLAMA_URL: str = "http://host.docker.internal:11434/api/generate"
    OLLAMA_MODEL: str = "llama3.2:1b"
    LLM_TEMPERATURE: float = 0.3
    LLM_TIMEOUT: int = 60  # Seconds to wait for a generation response
    LLM_NUM_PREDICT: int = 500  # Max tokens the model may generate
    LLM_MAX_STEPS: int = 10  # Max steps a generated workflow may contain

    # Logging settings
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    @classmethod
    def from_env(cls) -> "Config":
        """Create configuration from environment variables."""
        return cls(
            FLASK_ENV=os.getenv("FLASK_ENV", cls.FLASK_ENV),
            FLASK_DEBUG=os.getenv("FLASK_DEBUG", "true").lower() == "true",
            SECRET_KEY=os.getenv("SECRET_KEY", cls.SECRET_KEY),
            DATABASE_URL=os.getenv("DATABASE_URL", cls.DATABASE_URL),
            DATABASE_POOL_SIZE=int(os.getenv("DATABASE_POOL_SIZE", cls.DATABASE_POOL_SIZE)),
            DATABASE_MAX_OVERFLOW=int(os.getenv("DATABASE_MAX_OVERFLOW", cls.DATABASE_MAX_OVERFLOW)),
            REDIS_URL=os.getenv("REDIS_URL", cls.REDIS_URL),
            REDIS_MAX_CONNECTIONS=int(os.getenv("REDIS_MAX_CONNECTIONS", cls.REDIS_MAX_CONNECTIONS)),
            WORKER_CONCURRENCY=int(os.getenv("WORKER_CONCURRENCY", cls.WORKER_CONCURRENCY)),
            MAX_RETRIES=int(os.getenv("MAX_RETRIES", cls.MAX_RETRIES)),
            RETRY_BASE_DELAY=float(os.getenv("RETRY_BASE_DELAY", cls.RETRY_BASE_DELAY)),
            RETRY_MAX_DELAY=float(os.getenv("RETRY_MAX_DELAY", cls.RETRY_MAX_DELAY)),
            TASK_TIMEOUT=int(os.getenv("TASK_TIMEOUT", cls.TASK_TIMEOUT)),
            QUEUE_NAME=os.getenv("QUEUE_NAME", cls.QUEUE_NAME),
            QUEUE_PROCESSING_TIMEOUT=int(os.getenv("QUEUE_PROCESSING_TIMEOUT", cls.QUEUE_PROCESSING_TIMEOUT)),
            ALLOWED_HTTP_HOSTS=_parse_host_list(os.getenv("ALLOWED_HTTP_HOSTS")),
            OLLAMA_URL=os.getenv("OLLAMA_URL", cls.OLLAMA_URL),
            OLLAMA_MODEL=os.getenv("OLLAMA_MODEL", cls.OLLAMA_MODEL),
            LLM_TEMPERATURE=float(os.getenv("LLM_TEMPERATURE", cls.LLM_TEMPERATURE)),
            LLM_TIMEOUT=int(os.getenv("LLM_TIMEOUT", cls.LLM_TIMEOUT)),
            LLM_NUM_PREDICT=int(os.getenv("LLM_NUM_PREDICT", cls.LLM_NUM_PREDICT)),
            LLM_MAX_STEPS=int(os.getenv("LLM_MAX_STEPS", cls.LLM_MAX_STEPS)),
            LOG_LEVEL=os.getenv("LOG_LEVEL", cls.LOG_LEVEL),
            LOG_FORMAT=os.getenv("LOG_FORMAT", cls.LOG_FORMAT),
        )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Get cached configuration instance."""
    return Config.from_env()


class TestConfig(Config):
    """Configuration for testing environment."""
    
    FLASK_ENV: str = "testing"
    FLASK_DEBUG: bool = False
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/workflow_engine_test"
    REDIS_URL: str = "redis://localhost:6379/1"
    QUEUE_NAME: str = "workflow_tasks_test"
