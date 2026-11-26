"""Tests for provider model catalog discovery without live credentials."""

from types import SimpleNamespace

import httpx

from app.agent import model_catalog
from app.agent.chatgpt_account import chatgpt_account


def setup_function():
    model_catalog._compatible_models_cache.clear()


def teardown_function():
    model_catalog._compatible_models_cache.clear()


def test_parse_compatible_models_accepts_openai_and_ollama_shapes():
    parsed = model_catalog.parse_compatible_models({
        "data": [
            {"id": "qwen3.5:4b"},
            {"id": "deepseek/deepseek-chat"},
            {"id": "qwen3.5:4b"},
            {"id": "../invalid"},
            {"name": "fallback-model"},
            None,
        ],
    })

    assert [model.id for model in parsed] == [
        "qwen3.5:4b",
        "deepseek/deepseek-chat",
        "fallback-model",
    ]


def test_compatible_endpoint_discovery_sends_key_and_caches(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]},
            request=request,
        )

    monkeypatch.setattr(model_catalog.httpx, "get", fake_get)

    first = model_catalog.compatible_endpoint_models(
        "https://api.example.test/v1/",
        "fixture-secret",
    )
    second = model_catalog.compatible_endpoint_models(
        "https://api.example.test/v1",
        "fixture-secret",
    )

    assert [item.id for item in first] == ["deepseek-chat", "deepseek-reasoner"]
    assert second == first
    assert len(calls) == 1
    assert calls[0][0] == "https://api.example.test/v1/models"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer fixture-secret"
    assert "fixture-secret" not in str(first)


def test_model_options_include_discovered_and_manual_endpoint_models(monkeypatch):
    monkeypatch.setattr(model_catalog, "account_models", lambda: [])
    monkeypatch.setattr(
        chatgpt_account,
        "status",
        lambda: {"connected": False, "plan_usage": False},
    )
    monkeypatch.setattr(
        model_catalog,
        "compatible_endpoint_models",
        lambda base_url, api_key: [
            model_catalog.AccountModel(id="qwen3.5:4b", label="Qwen 3.5 4B"),
        ],
    )
    settings = SimpleNamespace(
        openai_api_key="",
        google_api_key="",
        compatible_api_key="fixture-secret",
    )

    payload = model_catalog.model_options_payload(
        {"openai_compatible": ["manual-model"]},
        settings=settings,
        compatible_base_url="http://localhost:11434/v1",
        compatible_api_key="fixture-secret",
    )

    compatible = next(
        item for item in payload["providers"] if item["id"] == "openai_compatible"
    )
    assert compatible["available"] is True
    assert compatible["source"] == "endpoint"
    assert compatible["models"] == ["qwen3.5:4b", "manual-model"]
    assert compatible["labels"]["qwen3.5:4b"] == "Qwen 3.5 4B"


def test_remote_compatible_discovery_is_skipped_without_saved_key(monkeypatch):
    monkeypatch.setattr(model_catalog, "account_models", lambda: [])
    monkeypatch.setattr(
        chatgpt_account,
        "status",
        lambda: {"connected": False, "plan_usage": False},
    )
    called = []
    monkeypatch.setattr(
        model_catalog,
        "compatible_endpoint_models",
        lambda *args: called.append(args) or [],
    )
    settings = SimpleNamespace(
        openai_api_key="",
        google_api_key="",
        compatible_api_key="",
    )

    payload = model_catalog.model_options_payload(
        {"openai_compatible": ["manual-model"]},
        settings=settings,
        compatible_base_url="https://api.example.test/v1",
    )

    compatible = next(
        item for item in payload["providers"] if item["id"] == "openai_compatible"
    )
    assert compatible["available"] is False
    assert compatible["models"] == ["manual-model"]
    assert called == []
