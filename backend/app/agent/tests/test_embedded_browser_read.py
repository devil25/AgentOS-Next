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
        "error": "The AgentOS embedded browser bridge is not available.",
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
        "timeout": 8,
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
