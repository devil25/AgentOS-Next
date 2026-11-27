import json

import pytest

from app.skills.browser_use import tools
from app.skills.browser_use.registration import register_browser_tools


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.payload


@pytest.mark.anyio
async def test_embedded_browser_read_requires_local_bridge_configuration(monkeypatch):
    monkeypatch.delenv("AGENTOS_EMBEDDED_BROWSER_PORT", raising=False)
    monkeypatch.delenv("AGENTOS_EMBEDDED_BROWSER_SECRET", raising=False)

    result = json.loads(await tools.embedded_browser_read())

    assert result == {
        "ok": False,
        "error": "Could not read the active AgentOS embedded browser page.",
    }


@pytest.mark.anyio
async def test_embedded_browser_read_uses_authenticated_loopback_and_truncates(monkeypatch):
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return _Response({
            "title": "ChatGPT",
            "url": "https://chatgpt.com/c/example",
            "headings": ["Conversation"],
            "text": "x" * 1200,
            "inspectedAt": "2026-11-27T12:00:00Z",
        })

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    result = json.loads(await tools.embedded_browser_read(max_chars=500))

    assert captured == {
        "url": "http://127.0.0.1:43210/v1/inspect",
        "authorization": "Bearer test-secret",
        "timeout": 12,
    }
    assert result["ok"] is True
    assert result["title"] == "ChatGPT"
    assert len(result["text"]) == 500
    assert result["truncated"] is True


@pytest.mark.anyio
async def test_embedded_browser_read_hides_bridge_errors(monkeypatch):
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")

    def fail(*_args, **_kwargs):
        raise OSError("private socket details")

    monkeypatch.setattr(tools.urllib.request, "urlopen", fail)
    result = json.loads(await tools.embedded_browser_read())

    assert result == {
        "ok": False,
        "error": "Could not read the active AgentOS embedded browser page.",
    }


def test_embedded_browser_read_is_registered_as_read_only_tool():
    registry = []
    register_browser_tools(registry, settings=object())

    tool = next(item for item in registry if item["name"] == "embedded_browser_read")
    assert tool["metadata"]["observation"] is True
    assert tool["metadata"]["mutates_state"] is False
    assert tool["metadata"]["risk_level"] == "low"
    assert tool["metadata"]["resource_locks"] == ["embedded_browser"]


@pytest.mark.anyio
async def test_embedded_browser_snapshot_uses_fixed_action_endpoint(monkeypatch):
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({
            "title": "ChatGPT",
            "url": "https://chatgpt.com/",
            "elements": [{"index": 0, "tag": "textarea", "editable": True}],
        })

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    result = json.loads(await tools.embedded_browser_snapshot())

    assert captured == {
        "url": "http://127.0.0.1:43210/v1/action",
        "body": {"action": "snapshot"},
    }
    assert result["ok"] is True
    assert result["elements"][0]["tag"] == "textarea"


@pytest.mark.anyio
async def test_embedded_browser_type_caps_text_and_sends_index(monkeypatch):
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({"ok": True, "action": "type", "index": 2, "characters": 4})

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    result = json.loads(await tools.embedded_browser_type(index=2, text="test"))

    assert captured["body"] == {"action": "type", "index": 2, "text": "test", "append": False}
    assert result["ok"] is True
    assert result["characters"] == 4


@pytest.mark.anyio
async def test_embedded_browser_press_key_rejects_unlisted_keys(monkeypatch):
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")

    result = json.loads(await tools.embedded_browser_press_key(key="F12"))

    assert result == {"ok": False, "error": "Unsupported key."}


def test_embedded_browser_interactions_are_registered_with_mutation_metadata():
    registry = []
    register_browser_tools(registry, settings=object())
    by_name = {item["name"]: item for item in registry}

    assert by_name["embedded_browser_snapshot"]["metadata"]["mutates_state"] is False
    for name in ("embedded_browser_click", "embedded_browser_type", "embedded_browser_press_key", "embedded_browser_navigate"):
        assert by_name[name]["metadata"]["mutates_state"] is True
        assert by_name[name]["metadata"]["parallel_safe"] is False
        assert by_name[name]["metadata"]["resource_locks"] == ["embedded_browser"]


@pytest.mark.anyio
async def test_embedded_browser_conversation_returns_bounded_structured_messages(monkeypatch):
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _Response({
            "ok": True,
            "provider": "chatgpt",
            "title": "ChatGPT",
            "url": "https://chatgpt.com/c/abc",
            "messages": [
                {"message_id": "u1", "role": "user", "text": "Hello"},
                {"message_id": "a1", "role": "assistant", "text": "Hi"},
                {"message_id": "ignored", "role": "system", "text": "not exposed"},
            ],
            "composerReady": True,
            "generating": False,
            "loginRequired": False,
        })

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    result = json.loads(await tools.embedded_browser_conversation())

    assert captured["url"] == "http://127.0.0.1:43210/v1/conversation"
    assert result["ok"] is True
    assert [item["role"] for item in result["messages"]] == ["user", "assistant"]
    assert result["composerReady"] is True


def test_embedded_conversation_tool_is_read_only_and_serialized():
    registry = []
    register_browser_tools(registry, settings=object())
    tool = next(item for item in registry if item["name"] == "embedded_browser_conversation")
    assert tool["metadata"]["observation"] is True
    assert tool["metadata"]["mutates_state"] is False
    assert tool["metadata"]["parallel_safe"] is False
    assert tool["metadata"]["resource_locks"] == ["embedded_browser"]
