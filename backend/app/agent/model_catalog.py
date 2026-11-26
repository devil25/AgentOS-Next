"""Model catalog for the settings UI: built-in models, the signed-in ChatGPT
account's own list, and models the user added by name."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

from app.agent.model_registry import CONNECTIONS, MODEL_LABELS, reasoning_efforts

logger = logging.getLogger(__name__)

# The account token reads the same catalog the Codex tools use. It is not a
# published API, so any failure falls back to the built-in list.
ACCOUNT_MODELS_URL = "https://api.openai.com/v1/models"
ACCOUNT_MODELS_TTL_SECONDS = 600
# Levels Monaw sends; the account also lists "ultra", which requests map to "max".
_REASONING_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

_account_cache: tuple[float, list["AccountModel"]] | None = None
_account_cache_identity = ""
_account_lock = threading.Lock()

@dataclass(frozen=True)
class AccountModel:
    id: str
    label: str
    reasoning_efforts: list[str] = field(default_factory=list)



_compatible_models_cache: dict[str, tuple[float, list["AccountModel"]]] = {}
_compatible_models_lock = threading.Lock()
COMPATIBLE_MODELS_TTL_SECONDS = 60


def parse_compatible_models(payload: object) -> list[AccountModel]:
    """Read model IDs from standard OpenAI-compatible and Ollama-style lists."""
    if not isinstance(payload, dict):
        return []
    entries = payload.get("data")
    if not isinstance(entries, list):
        entries = payload.get("models")
    if not isinstance(entries, list):
        return []

    models: list[AccountModel] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id") or entry.get("name") or entry.get("model")
        if not isinstance(identifier, str):
            continue
        identifier = identifier.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", identifier) or identifier in seen:
            continue
        seen.add(identifier)
        display_name = entry.get("display_name")
        label = display_name.strip() if isinstance(display_name, str) and display_name.strip() else identifier
        models.append(AccountModel(id=identifier, label=label[:160]))
    return models


def compatible_endpoint_models(base_url: str, api_key: str = "") -> list[AccountModel]:
    """Discover chat model IDs without exposing credentials to the UI.

    A short cache avoids repeatedly querying a provider while the user browses
    settings. Failures preserve the last successful catalog and fall back to
    manually entered model IDs.
    """
    endpoint = str(base_url or "").strip().rstrip("/")
    if not endpoint:
        return []
    secret = str(api_key or "").strip()
    cache_key = hashlib.sha256(f"{endpoint}\0{secret}".encode("utf-8")).hexdigest()
    now = time.monotonic()
    with _compatible_models_lock:
        cached = _compatible_models_cache.get(cache_key)
        if cached and now - cached[0] < COMPATIBLE_MODELS_TTL_SECONDS:
            return list(cached[1])

    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    try:
        response = httpx.get(
            f"{endpoint}/models",
            headers=headers,
            timeout=httpx.Timeout(3.0, connect=1.0),
            follow_redirects=False,
        )
        response.raise_for_status()
        models = parse_compatible_models(response.json())
    except Exception as exc:  # noqa: BLE001 - catalog discovery is optional
        logger.info("Could not discover models from the configured compatible endpoint: %s", exc)
        with _compatible_models_lock:
            stale = _compatible_models_cache.get(cache_key)
        return list(stale[1]) if stale else []

    if models:
        with _compatible_models_lock:
            _compatible_models_cache[cache_key] = (time.monotonic(), models)
    return models




def _account_identity() -> str:
    """Read the signed-in identity before consulting account-specific metadata."""
    from app.agent.chatgpt_account import chatgpt_account

    return chatgpt_account.catalog_identity()


def parse_account_models(payload: object) -> list[AccountModel]:
    """The models an account offers in its picker, in the account's order."""
    entries = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(entries, list) and isinstance(payload, dict) and isinstance(payload.get("data"), list):
        # Standard /v1/models also lists embeddings/audio/image models. Only
        # known chat IDs are eligible; no reasoning contract is implied.
        chat_ids = set(CONNECTIONS["codex"].models)
        return [AccountModel(id=entry["id"].strip(), label=MODEL_LABELS.get(entry["id"].strip(), entry["id"].strip()))
                for entry in payload["data"] if isinstance(entry, dict)
                and isinstance(entry.get("id"), str) and entry["id"].strip() in chat_ids]
    if not isinstance(entries, list):
        return []
    listed = [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("visibility") == "list" and str(entry.get("slug") or "").strip()
    ]
    listed.sort(key=lambda entry: entry.get("priority") if isinstance(entry.get("priority"), (int, float)) else 1_000)
    models = []
    for entry in listed:
        levels = entry.get("supported_reasoning_levels") or []
        efforts = [
            str(level.get("effort") if isinstance(level, dict) else level)
            for level in levels if isinstance(levels, list)
        ]
        models.append(AccountModel(
            id=str(entry["slug"]).strip(),
            label=str(entry.get("display_name") or entry["slug"]).strip(),
            reasoning_efforts=[effort for effort in _REASONING_LEVELS if effort in efforts],
        ))
    return models


def _fetch_account_models() -> list[AccountModel] | None:
    """None when no ChatGPT account is signed in."""
    from app.agent.chatgpt_account import chatgpt_account

    if not chatgpt_account.status().get("connected"):
        return None
    token = chatgpt_account.access_token()
    response = httpx.get(ACCOUNT_MODELS_URL, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    response.raise_for_status()
    return parse_account_models(response.json())


def account_models(*, refresh: bool = False) -> list[AccountModel]:
    """The signed-in account's models, cached briefly; empty when unavailable."""
    global _account_cache, _account_cache_identity
    identity = _account_identity()
    with _account_lock:
        if identity != _account_cache_identity or not identity:
            _account_cache = None
            _account_cache_identity = identity
        if not identity:
            return []
        if not refresh and _account_cache is not None and time.monotonic() - _account_cache[0] < ACCOUNT_MODELS_TTL_SECONDS:
            return _account_cache[1]
    try:
        models = _fetch_account_models()
    except Exception as exc:  # noqa: BLE001 - any failure means the built-in list
        logger.warning("Could not load the ChatGPT account's models: %s", exc)
        models = []
    with _account_lock:
        if identity != _account_identity():
            return []  # A completed request from an old account cannot refill the cache.
        if models is None:
            _account_cache = None  # signed out
            return []
        # Only a good list is cached, so a sign-in shows at once and an outage
        # keeps the last good list.
        if models:
            _account_cache = (time.monotonic(), models)
        return _account_cache[1] if _account_cache is not None else []


def model_options_payload(
    custom_models: dict[str, list[str]] | None = None,
    *,
    settings=None,
    compatible_base_url: str = "",
    compatible_api_key: str = "",
) -> dict:
    """Publish one secret-free registry for every model selection surface."""
    from app.agent.chatgpt_account import chatgpt_account

    custom_models = custom_models or {}
    live = account_models()
    account = chatgpt_account.status()
    providers = []
    for provider, connection in CONNECTIONS.items():
        provider_base_url = compatible_base_url if provider == "openai_compatible" else ""
        available, reason = connection.readiness(
            settings,
            account_connected=bool(account.get("connected") and account.get("plan_usage")),
            base_url=provider_base_url,
        )
        models = list(connection.models)
        labels = {model: MODEL_LABELS.get(model, model) for model in models}
        efforts = {model: reasoning_efforts(provider, model, provider_base_url) for model in models}
        source = "built_in"
        if provider == "codex" and live:
            models = list(dict.fromkeys(model.id for model in live))
            labels = {model.id: model.label for model in live}
            efforts = {model.id: model.reasoning_efforts for model in live}
            source = "account"
        elif provider == "openai_compatible" and available and provider_base_url:
            discovered = compatible_endpoint_models(provider_base_url, compatible_api_key)
            if discovered:
                models = list(dict.fromkeys(model.id for model in discovered))
                labels = {model.id: model.label for model in discovered}
                efforts = {
                    model.id: reasoning_efforts(provider, model.id, provider_base_url)
                    for model in discovered
                }
                source = "endpoint"
        for model in custom_models.get(provider, []):
            if model not in models:
                models.append(model)
                labels[model] = MODEL_LABELS.get(model, model)
                efforts[model] = reasoning_efforts(provider, model, provider_base_url)
        providers.append({
            "id": provider, "connection_id": connection.id, "label": connection.label,
            "protocol": connection.protocol, "available": available, "unavailable_reason": reason,
            "models": models, "labels": labels, "reasoning_efforts": efforts, "source": source,
        })
    return {"providers": providers}
