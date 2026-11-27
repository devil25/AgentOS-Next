import json

import pytest

from app.agent.web_chat.adapter import WebChatAdapterError
from app.agent.web_chat.embedded_port import EmbeddedWebChatBrowserPort


def _tool_result(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


@pytest.mark.anyio
async def test_embedded_port_inspects_structured_chatgpt_conversation_through_tool_runner():
    calls = []

    async def execute(name, arguments):
        calls.append((name, arguments))
        return _tool_result({
            "ok": True,
            "provider": "chatgpt",
            "title": "ChatGPT",
            "url": "https://chatgpt.com/c/abc",
            "messages": [
                {"message_id": "u1", "role": "user", "text": "Hello", "complete": True},
                {"message_id": "a1", "role": "assistant", "text": "Hi", "complete": True},
            ],
            "composerReady": True,
            "generating": False,
            "loginRequired": False,
            "blockedReason": "",
        })

    port = EmbeddedWebChatBrowserPort("chatgpt", execute)
    snapshot = await port.inspect(None)

    assert calls == [("embedded_browser_conversation", {})]
    assert port.resolved_page_id == 1
    assert snapshot.url == "https://chatgpt.com/c/abc"
    assert snapshot.composer_ready is True
    assert [message.role for message in snapshot.messages] == ["user", "assistant"]


@pytest.mark.anyio
async def test_embedded_port_rejects_provider_mismatch_before_submission():
    async def execute(_name, _arguments):
        return _tool_result({
            "ok": True, "provider": "gemini", "url": "https://gemini.google.com/app",
            "messages": [], "composerReady": True,
        })

    port = EmbeddedWebChatBrowserPort("chatgpt", execute)
    with pytest.raises(WebChatAdapterError, match="does not match"):
        await port.inspect(None)


@pytest.mark.anyio
async def test_embedded_port_submits_only_to_one_editable_composer():
    calls = []

    async def execute(name, arguments):
        calls.append((name, arguments))
        if name == "embedded_browser_snapshot":
            return _tool_result({
                "ok": True,
                "elements": [
                    {"index": 0, "tag": "textarea", "editable": True, "disabled": False,
                     "type": "textarea", "placeholder": "Message ChatGPT"},
                ],
            })
        return _tool_result({"ok": True, "action": name})

    port = EmbeddedWebChatBrowserPort("chatgpt", execute)
    await port.submit(1, "Perform a harmless test")

    assert calls == [
        ("embedded_browser_snapshot", {}),
        ("embedded_browser_type", {"index": 0, "text": "Perform a harmless test"}),
        ("embedded_browser_press_key", {"key": "Enter"}),
    ]


@pytest.mark.anyio
async def test_embedded_port_refuses_ambiguous_composers_without_typing():
    calls = []

    async def execute(name, arguments):
        calls.append((name, arguments))
        return _tool_result({
            "ok": True,
            "elements": [
                {"index": 0, "editable": True, "disabled": False, "type": "textarea"},
                {"index": 1, "editable": True, "disabled": False, "type": "textarea"},
            ],
        })

    port = EmbeddedWebChatBrowserPort("chatgpt", execute)
    with pytest.raises(WebChatAdapterError, match="exactly one"):
        await port.submit(1, "Do not send")
    assert [name for name, _ in calls] == ["embedded_browser_snapshot"]
