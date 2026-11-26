"""Connection identities and model metadata shared by settings, UI and runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

ProviderId = Literal["openai", "gemini", "codex", "openai_compatible"]
OptionalProviderId = ProviderId | Literal[""]


@dataclass(frozen=True)
class ModelConnection:
    """A stable connection identity, independent of its API protocol."""

    id: ProviderId
    label: str
    protocol: str
    models: tuple[str, ...]
    api_key_attribute: str = ""
    requires_base_url: bool = False

    def api_key(self, settings: Any) -> str:
        """Resolve credentials internally without including them in the catalog."""
        return str(getattr(settings, self.api_key_attribute, "") or "") if self.api_key_attribute else ""

    def readiness(self, settings: Any, *, account_connected: bool = False, base_url: str = "") -> tuple[bool, str]:
        """Report locally configured credentials, not live service availability."""
        key = self.api_key(settings).strip() if self.api_key_attribute else ""
        local_endpoint = bool(self.requires_base_url and str(base_url or "").strip().lower().startswith("http://"))
        key_required = bool(self.api_key_attribute) and not local_endpoint
        ready = bool(key) if key_required else (True if self.requires_base_url else account_connected)
        if self.requires_base_url and not str(base_url or "").strip():
            ready, reason = False, "Add an API base URL in Settings."
        elif key_required and not key:
            ready, reason = False, "Add an API key in Settings."
        else:
            reason = ""
        return ready, "" if ready else reason


OPENAI_MODELS = (
    "gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra",
    "gpt-6.2-sol", "gpt-6.2-astra", "gpt-6.1-terra",
)
GEMINI_4_MODELS = ("gemini-4-neon", "gemini-4-hydrogen", "gemini-4-carbon", "gemini-4-argon")
GEMINI_MODELS = (
    "gemini-pro-latest", "gemini-flash-latest", "gemini-flash-lite-latest",
    *GEMINI_4_MODELS,
)
MODEL_LABELS = {
    "gpt-6-luna": "GPT-6 Luna",
    "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-6.2-sol": "GPT-6.2 Sol",
    "gpt-6.2-astra": "GPT-6.2 Astra",
    "gpt-6.1-terra": "GPT-6.1 Terra",
    "gemini-pro-latest": "Gemini Pro Latest",
    "gemini-flash-latest": "Gemini Flash Latest",
    "gemini-flash-lite-latest": "Gemini Flash Lite Latest",
    "gemini-4-neon": "Gemini 4 Neon",
    "gemini-4-hydrogen": "Gemini 4 Hydrogen",
    "gemini-4-carbon": "Gemini 4 Carbon",
    "gemini-4-argon": "Gemini 4 Argon",
    "deepseek-flash": "DeepSeek V4.1 Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "glm-5.3": "GLM-5.3",
    "glm-5.3-flash": "GLM-5.3 Flash",
    "glm-5.4": "GLM-5.4",
    "openrouter/free": "OpenRouter Free Router",
}
CONNECTIONS = {
    "openai": ModelConnection("openai", "OpenAI API", "openai_responses", OPENAI_MODELS, "openai_api_key"),
    "codex": ModelConnection("codex", "OpenAI account", "chatgpt_account", OPENAI_MODELS),
    "gemini": ModelConnection("gemini", "Google", "gemini_generate_content", GEMINI_MODELS, "google_api_key"),
    "openai_compatible": ModelConnection(
        "openai_compatible", "OpenAI-compatible API", "openai_chat_completions", (),
        "compatible_api_key", requires_base_url=True,
    ),
}


def get_connection(connection_id: str) -> ModelConnection:
    """Resolve a registered connection; never route an unknown ID to a default."""
    try:
        return CONNECTIONS[str(connection_id).strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported model connection: {connection_id!r}.") from exc


def reasoning_efforts(connection_id: str, model_name: str, base_url: str = "") -> list[str]:
    """Return documented controls for known models; unknown models expose none."""
    connection = get_connection(connection_id)
    model = model_name.lower()
    if connection.protocol == "openai_chat_completions":
        hostname = (urlsplit(base_url).hostname or "").lower() if base_url else ""
        if hostname == "api.deepseek.com" and model in {"deepseek-flash", "deepseek-v4-pro"}:
            return ["none", "low", "medium", "high", "max"]
        if hostname == "api.z.ai" and model.startswith(("glm-5.3", "glm-5.3-flash")):
            return ["low", "medium", "high", "max"]
        if hostname == "api.z.ai" and model.startswith("glm-5.2"):
            return ["none", "low", "medium", "high", "max"]
        return []  # Unknown compatibility endpoints expose no guessed controls.
    if connection.protocol == "gemini_generate_content":
        if model in {"gemini-4-neon", "gemini-4-hydrogen"}:
            return ["low", "medium", "high", "xhigh"]
        if model in {"gemini-4-carbon", "gemini-4-argon"}:
            return ["low", "medium", "high"]
        if model in {"gemini-pro-latest", "gemini-flash-latest"}:
            return ["low", "medium", "high"]
        if model == "gemini-flash-lite-latest":
            return ["minimal", "low", "medium", "high"]
        if model == "gemini-2.5-pro":
            return ["low", "medium", "high"]
        if model in {"gemini-2.5-flash", "gemini-2.5-flash-lite"}:
            return ["none", "low", "medium", "high"]
        return []
    if model in OPENAI_MODELS or model == "gpt-6-sol":
        levels = ["none", "low", "medium", "high", "xhigh", "max"]
        return levels if model in {"gpt-6-luna", "gpt-6.1-terra"} else levels[1:]
    return []


def normalize_reasoning_effort(connection_id: str, model_name: str, effort: str, base_url: str = "") -> str:
    """Normalize known controls while retaining explicit choices for unknown models."""
    effort = "max" if effort == "ultra" else effort
    levels = reasoning_efforts(connection_id, model_name, base_url)
    if not levels or effort in levels:
        return effort
    if effort == "minimal" and "low" in levels:
        return "low"  # A legacy minimum must not turn reasoning off.
    if effort in {"xhigh", "max"} and "high" in levels:
        return levels[-1]  # Retain the highest supported level without disabling thinking.
    if effort in {"none", "minimal"}:
        return levels[0]
    return "medium" if "medium" in levels else levels[0]


def initialize_provider_client(client: Any) -> None:
    """Initialize the SDK chosen by the connection's protocol."""
    protocol = get_connection(client.provider).protocol
    if protocol == "gemini_generate_content":
        from google import genai

        client._genai_client = genai.Client(api_key=client.api_key) if client.api_key else None
    elif protocol == "openai_responses":
        from openai import AsyncOpenAI

        client._openai_client = AsyncOpenAI(api_key=client.api_key, max_retries=0)
    elif protocol == "chatgpt_account":
        client._chatgpt_token = ""
    elif protocol == "openai_chat_completions":
        from openai import AsyncOpenAI

        if not client.base_url:
            raise ValueError("OpenAI-compatible API requires an API base URL in Settings.")
        client._openai_client = AsyncOpenAI(
            api_key=client.api_key or "local-openai-compatible",
            base_url=client.base_url,
            max_retries=0,
        )
    else:
        raise ValueError(f"Unsupported model protocol: {protocol!r}.")
