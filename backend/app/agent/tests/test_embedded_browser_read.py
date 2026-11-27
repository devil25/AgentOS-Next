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
        "status": "error",
        "reason_code": "embedded_browser_read_failed",
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
        "status": "error",
        "reason_code": "embedded_browser_read_failed",
        "error": "private socket details",
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
async def test_embedded_browser_type_reports_context_error_as_recoverable_tool_failure(monkeypatch):
    from io import BytesIO

    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")

    def fail(*_args, **_kwargs):
        raise tools.urllib.error.HTTPError(
            "http://127.0.0.1:43210/v1/action",
            409,
            "Conflict",
            {},
            BytesIO(json.dumps({
                "error": "The provider page execution context changed during type.",
                "code": "browser_execution_context_changed",
            }).encode("utf-8")),
        )

    monkeypatch.setattr(tools.urllib.request, "urlopen", fail)
    result = json.loads(await tools.embedded_browser_type(index=1, text="hello"))

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["reason_code"] == "embedded_browser_type_failed"
    assert "execution context changed" in result["error"]


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
            "captureSource": "accessibility_tree",
            "accessibilityNodeCount": 4812,
            "composerReady": True,
            "generating": False,
            "loginRequired": False,
        })

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    result = json.loads(await tools.embedded_browser_conversation())

    assert captured["url"] == "http://127.0.0.1:43210/v1/conversation"
    assert result["ok"] is True
    assert [item["role"] for item in result["messages"]] == ["user", "assistant"]
    assert result["messageCount"] == 2
    assert result["totalMessageCount"] == 2
    assert result["captureTruncated"] is False
    assert result["captureSource"] == "accessibility_tree"
    assert result["accessibilityNodeCount"] == 4812
    assert result["pageText"] == ""
    assert result["composerReady"] is True


def test_conversation_read_budget_resets_after_successful_browser_mutation_only():
    from app.agent.harness.tool_policy import ToolPolicy
    from app.agent.harness.tool_protocol import ToolCallRequest

    class Registry:
        @staticmethod
        def get_tool(name):
            return {"name": name, "metadata": {"repeat_safe": False}} if name == "embedded_browser_conversation" else None

    policy = ToolPolicy(max_identical_calls=2)
    registry = Registry()
    read = ToolCallRequest("read", "embedded_browser_conversation", {})
    assert policy.decide(request=read, registry=registry).allowed
    assert policy.decide(request=read, registry=registry).allowed
    assert not policy.decide(request=read, registry=registry).allowed

    # An observation alone must not permit another unchanged read.
    policy.note_successful_page_action("embedded_browser_snapshot")
    assert not policy.decide(request=read, registry=registry).allowed

    # A completed mutation changes the page, so one new capture cycle is valid.
    policy.note_successful_page_action("embedded_browser_type")
    assert policy.decide(request=read, registry=registry).allowed
    assert policy.decide(request=read, registry=registry).allowed
    assert not policy.decide(request=read, registry=registry).allowed


def test_embedded_conversation_tool_is_read_only_and_serialized():
    registry = []
    register_browser_tools(registry, settings=object())
    tool = next(item for item in registry if item["name"] == "embedded_browser_conversation")
    assert tool["metadata"]["observation"] is True
    assert tool["metadata"]["mutates_state"] is False
    assert tool["metadata"]["parallel_safe"] is False
    assert tool["metadata"]["resource_locks"] == ["embedded_browser"]


@pytest.mark.anyio
async def test_embedded_browser_conversation_exposes_visible_text_fallback_when_messages_are_missing(monkeypatch):
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")

    def fake_urlopen(request, timeout):
        return _Response({
            "ok": True,
            "provider": "chatgpt",
            "title": "ChatGPT",
            "url": "https://chatgpt.com/c/abc",
            "messages": [],
            "captureSource": "visible_page_text_fallback",
            "accessibilityNodeCount": 0,
            "pageText": "The visible assistant answer",
            "composerReady": True,
            "generating": False,
        })

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    result = json.loads(await tools.embedded_browser_conversation())

    assert result["messages"] == []
    assert result["messageCount"] == 0
    assert result["captureSource"] == "visible_page_text_fallback"
    assert result["pageText"] == "The visible assistant answer"


def test_embedded_conversation_observation_cannot_repeat_forever():
    from app.agent.harness.tool_policy import ToolPolicy
    from app.agent.harness.tool_protocol import ToolCallRequest

    class Registry:
        @staticmethod
        def get_tool(name):
            return {
                "name": name,
                "metadata": {
                    "observation": True,
                    "mutates_state": False,
                    "risk_level": "low",
                    "repeat_safe": False,
                },
            } if name == "embedded_browser_conversation" else None

    policy = ToolPolicy(max_identical_calls=2)
    registry = Registry()
    call = ToolCallRequest("call-1", "embedded_browser_conversation", {})
    assert policy.decide(request=call, registry=registry).allowed is True
    assert policy.decide(request=call, registry=registry).allowed is True
    decision = policy.decide(request=call, registry=registry)
    assert decision.allowed is False
    assert decision.metadata["code"] == "repeated_tool_call_blocked"



@pytest.mark.anyio
async def test_embedded_browser_conversation_keeps_only_latest_exchange_and_bounds_long_reply(monkeypatch):
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_PORT", "43210")
    monkeypatch.setenv("AGENTOS_EMBEDDED_BROWSER_SECRET", "test-secret")
    messages = [
        {"message_id": "old-user", "role": "user", "text": "old question"},
        {"message_id": "old-assistant", "role": "assistant", "text": "old answer"},
        {"message_id": "latest-user", "role": "user", "text": "latest question"},
        {"message_id": "latest-assistant", "role": "assistant", "text": "A" * 30000},
    ]

    def fake_urlopen(request, timeout):
        return _Response({
            "ok": True,
            "provider": "chatgpt",
            "title": "ChatGPT",
            "url": "https://chatgpt.com/c/abc",
            "messages": messages,
            "captureSource": "accessibility_tree",
            "pageText": "duplicate page evidence should not be returned",
        })

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    result = json.loads(await tools.embedded_browser_conversation())

    assert [item["message_id"] for item in result["messages"]] == ["latest-user", "latest-assistant"]
    assert result["messages"][0]["text"] == "latest question"
    assert len(result["messages"][1]["text"]) < 16500
    assert result["totalMessageCount"] == 4
    assert result["messageCount"] == 2
    assert result["captureTruncated"] is True
    assert result["pageText"] == ""


def test_browser_workflow_tells_agent_to_consume_conversation_capture_once():
    from app.agent.harness.browser_workflow import BrowserWorkflow

    workflow = BrowserWorkflow()
    output = json.dumps({
        "ok": True,
        "messages": [{"role": "assistant", "text": "The captured answer"}],
        "pageText": "",
    })
    hint = workflow.observe("embedded_browser_conversation", {}, output, "ok")

    assert "already captured above" in hint
    assert "answer the user now" in hint
    assert workflow.observe("embedded_browser_conversation", {}, output, "ok") == ""
