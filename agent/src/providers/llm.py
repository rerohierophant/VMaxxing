"""LLM factory — builds the configured OpenAI-compatible chat client.

Replaces the former langchain-openai provider layer. The model is reached
through :class:`src.providers.openai_compat.OpenAICompatClient`, which speaks the
OpenAI ``/v1/chat/completions`` protocol directly and works with any
OpenAI-compatible endpoint (OpenAI, DeepSeek, Ollama, Qwen, …).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from src.config.accessor import get_env_config, reset_env_config
from src.providers.capabilities import (
    get_llm_credentials,
    get_provider_capabilities,
)
from src.providers.openai_compat import OpenAICompatClient

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore


AGENT_DIR = Path(__file__).resolve().parents[2]

# .env search order: ~/.vmx/.env → agent/.env → $CWD/.env
_ENV_CANDIDATES = [
    Path.home() / ".vmx" / ".env",
    AGENT_DIR / ".env",
    Path.cwd() / ".env",
]

# Index-aligned with _ENV_CANDIDATES. CWE-209: never log the absolute
# .env path (it leaks the OS username / home / CWD). The label names
# which slot won - the entire P08 R1 signal - using compile-time
# constants only.
_ENV_LABELS = ("~/.vmx/.env", "<AGENT_DIR>/.env", "<CWD>/.env")

# Kimi reasoning models (K-series) reject any temperature other than 1 with
# "invalid temperature: only 1 is allowed for this model".
_KIMI_FORCED_TEMPERATURE_RE = re.compile(r"kimi-(k\d+|for-coding)", re.IGNORECASE)

logger = logging.getLogger(__name__)

_dotenv_loaded: bool = False


def _redact_env_source(loaded: Path | None) -> str:
    """Map a resolved `.env` candidate to a stable, leak-free label.

    Returns a symbolic slot label (never the absolute path) so a stale
    or shadowed `.env` stays diagnosable without exposing the OS
    username, home, or CWD (CWE-209). A candidate outside the fixed
    list (e.g. one injected by a test) collapses to a generic
    placeholder rather than echoing a real path.
    """
    if loaded is None:
        return "none (no .env file found)"
    for label, candidate in zip(_ENV_LABELS, _ENV_CANDIDATES):
        if loaded == candidate:
            return label
    return "<.env>"


def _redact_base_url_for_log(raw: str | None) -> str:
    """Return a diagnostic-safe base URL label for logs."""
    if not raw or not raw.strip():
        return "(unset)"

    try:
        parsed = urlsplit(raw.strip())
    except ValueError:
        return "<base-url>"

    if not parsed.scheme or not parsed.hostname:
        return "<base-url>"

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"

    return f"{parsed.scheme}://{host}"


def _package_version(package: str) -> str:
    """Return an installed package version or a stable missing label."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(package)
    except PackageNotFoundError:
        return "not_installed"


def _redact_env_flag(name: str) -> str:
    """Report whether an env var is set without exposing its value."""
    value = os.getenv(name, "")  # noqa: env-gate — diagnostic redaction helper
    return "set" if value else "unset"


def _redact_proxy_url(name: str, raw: str | None) -> str:
    """Return a credential-free proxy URL label."""
    if not raw:
        return "unset"
    if name.upper().endswith("NO_PROXY"):
        return "set"
    return _redact_base_url_for_log(raw)


def _load_env_file(path: Path) -> None:
    """Load a single .env file into os.environ (setdefault, no override)."""
    if load_dotenv is not None:
        load_dotenv(dotenv_path=path, override=False)
    else:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _ensure_dotenv() -> None:
    """Load `.env` from the first found candidate path."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    loaded = None
    for candidate in _ENV_CANDIDATES:
        if candidate.exists():
            _load_env_file(candidate)
            loaded = candidate
            break
    if loaded is not None:
        reset_env_config()
    _dotenv_loaded = True
    # P08 R1: one-time, behavior-preserving diagnostic so a stale or
    # shadowed .env is observable instead of costing hours. The path is
    # redacted to a symbolic slot label and the API key is never logged.
    logger.info(
        "dotenv resolved from %s | provider=%s model=%s base=%s",
        _redact_env_source(loaded),
        get_env_config().llm.langchain_provider,
        get_env_config().llm.langchain_model_name or "(unset)",
        _redact_base_url_for_log(
            os.getenv("OPENAI_BASE_URL")  # noqa: env-gate — diagnostic display
            or os.getenv("OPENAI_API_BASE")  # noqa: env-gate — diagnostic display
        ),
    )


def _normalize_ollama_base_url(base_url: str) -> str:
    """Append ``/v1`` when missing so the client hits Ollama's OpenAI-compatible API."""
    url = base_url.strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"


def _sync_provider_env() -> None:
    """Map provider-specific env vars to OPENAI_* for the OpenAI-compatible client.

    Each entry: provider_name -> (api_key_env, base_url_env).
    Base URLs come from .env; when unset, ``get_llm_credentials`` falls back to
    the provider catalog's ``default_base_url`` (see ``capabilities.py``).
    api_key_env=None means no key required (e.g. Ollama local).
    """
    _ensure_dotenv()
    reset_env_config()
    provider = get_env_config().llm.langchain_provider.lower()

    creds = get_llm_credentials(provider, get_env_config().llm.langchain_model_name)
    api_key = creds["api_key"]
    base_url = creds["base_url"]

    if provider == "ollama" and base_url:
        base_url = _normalize_ollama_base_url(base_url)

    # SDK-side env setup, not VMaxxing config reads
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_API_BASE"] = base_url
        os.environ["OPENAI_BASE_URL"] = base_url


def provider_diagnostics() -> dict[str, Any]:
    """Build a redacted provider diagnostic snapshot.

    Returns:
        Redacted provider/model/package/env/capability details.
    """
    _sync_provider_env()
    provider = get_env_config().llm.langchain_provider.strip().lower()
    model = get_env_config().llm.langchain_model_name.strip()
    caps = get_provider_capabilities(provider, model)
    key_env = caps.api_key_env
    creds = get_llm_credentials(provider, model)
    base_url = creds["base_url"]
    proxy_names = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ]
    package_names = ["openai"]
    return {
        "provider": provider,
        "model": model,
        "base_url": _redact_base_url_for_log(base_url),
        "api_key": {key_env: _redact_env_flag(key_env)} if key_env else {},
        "env": {
            "LANGCHAIN_PROVIDER": _redact_env_flag("LANGCHAIN_PROVIDER"),
            "LANGCHAIN_MODEL_NAME": _redact_env_flag("LANGCHAIN_MODEL_NAME"),
            "OPENAI_API_KEY": _redact_env_flag("OPENAI_API_KEY"),
            "OPENAI_BASE_URL": _redact_base_url_for_log(
                os.getenv("OPENAI_BASE_URL")  # noqa: env-gate — diagnostic snapshot
            ),
            "OPENAI_API_BASE": _redact_base_url_for_log(
                os.getenv("OPENAI_API_BASE")  # noqa: env-gate — diagnostic snapshot
            ),
        },
        "proxy": {
            name: _redact_proxy_url(
                name, os.getenv(name)  # noqa: env-gate — proxy env iteration
            )
            for name in proxy_names
            if os.getenv(name)  # noqa: env-gate — proxy env filter
        },
        "packages": {name: _package_version(name) for name in package_names},
        "timeout_seconds": get_env_config().llm.timeout_seconds,
        "max_retries": get_env_config().llm.max_retries,
        "reasoning_effort": get_env_config()
        .llm.langchain_reasoning_effort.strip()
        .lower(),
        "adapter": {
            "type": "openai-compatible",
            "mode": "openai-compatible",
            "native_package": None,
            "native_package_version": None,
        },
        "capabilities": {
            "capture_reasoning": caps.capture_reasoning,
            "send_reasoning_content": caps.send_reasoning_content,
            "gemini_thought_signatures": caps.gemini_thought_signatures,
            "openrouter_reasoning_body": caps.openrouter_reasoning_body,
        },
    }


def build_llm(*, model_name: Optional[str] = None, callbacks: Any = None) -> Any:
    """Construct the configured OpenAI-compatible chat client.

    Args:
        model_name: Model name; defaults to ``LANGCHAIN_MODEL_NAME``.
        callbacks: Accepted for signature compatibility; unused by the
            OpenAI-compatible client (streaming callbacks are passed directly
            to ``ChatLLM.stream_chat``).

    Returns:
        An :class:`OpenAICompatClient` instance.

    Raises:
        RuntimeError: If the model name is unset or the ``openai`` SDK is missing.
    """
    _sync_provider_env()
    name = model_name or get_env_config().llm.langchain_model_name.strip()
    if not name:
        raise RuntimeError("LANGCHAIN_MODEL_NAME is not set")
    temperature = get_env_config().llm.langchain_temperature
    provider = get_env_config().llm.langchain_provider.lower()
    caps = get_provider_capabilities(provider, name)
    creds = get_llm_credentials(provider, name)
    api_key = creds["api_key"]
    base_url = creds["base_url"]

    if provider == "ollama" and base_url:
        base_url = _normalize_ollama_base_url(base_url)

    # MiniMax requires temperature in (0.0, 1.0]; clamp the default 0.0 to 0.01
    # to avoid an API validation error.
    if provider == "minimax" and temperature <= 0.0:
        temperature = 0.01

    # Kimi reasoning models reject any temperature other than 1
    # ("invalid temperature: only 1 is allowed for this model").
    if (
        caps.name in {"moonshot", "kimi-coding"}
        and _KIMI_FORCED_TEMPERATURE_RE.match(name)
        and temperature != 1.0
    ):
        logger.info("Forcing temperature=1.0 for %s (provider requirement)", name)
        temperature = 1.0

    reasoning_effort = get_env_config().llm.langchain_reasoning_effort.strip().lower() or None

    # Optional reasoning activation for relays requiring opt-in (OpenRouter).
    # Moonshot/DeepSeek official APIs emit reasoning by default and ignore this field.
    extra_body = (
        {"reasoning": {"effort": reasoning_effort}}
        if reasoning_effort and caps.openrouter_reasoning_body
        else None
    )
    default_headers = None
    if caps.default_headers:
        headers = dict(caps.default_headers)
        if caps.name in {"moonshot", "kimi-coding"}:
            custom_ua = get_env_config().llm.moonshot_user_agent.strip()
            if custom_ua:
                headers["User-Agent"] = custom_ua
        default_headers = headers

    max_tokens = None
    if provider == "anthropic":
        configured = get_env_config().llm.anthropic_max_tokens
        if configured:
            max_tokens = configured

    client_kwargs = {
        "api_key": api_key or None,
        "base_url": base_url or None,
        "model": name,
        "temperature": temperature,
        "timeout": get_env_config().llm.timeout_seconds,
        "max_retries": get_env_config().llm.max_retries,
        "caps": caps,
        "reasoning_effort": reasoning_effort,
        "extra_body": extra_body,
        "vibe_provider": provider,
    }
    if default_headers is not None:
        client_kwargs["default_headers"] = default_headers
    if max_tokens is not None:
        client_kwargs["max_tokens"] = max_tokens
    return OpenAICompatClient(**client_kwargs)
