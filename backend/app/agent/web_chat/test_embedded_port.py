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
async def test_embedded_port_starts_new_chat_only_when_control_and_empty_state_are_confirmed():
    calls = []
    conversation_reads = [
        {"ok": True, "provider": "chatgpt", "url": "https://chatgpt.com/c/old",
         "messages": [{"message_id": "a1", "role": "assistant", "text": "Old context"}],
         "composerReady": True},
        {"ok": True, "provider": "chatgpt", "url": "https://chatgpt.com/",
         "messages": [], "composerReady": True},
    ]

    async def execute(name, arguments):
        calls.append((name, arguments))
        if name == "embedded_browser_conversation":
            return _tool_result(conversation_reads.pop(0))
        if name == "embedded_browser_snapshot":
            return _tool_result({"ok": True, "elements": [
                {"index": 4, "tag": "button", "label": "New chat", "text": ""},
            ]})
        if name == "embedded_browser_click":
            return _tool_result({"ok": True, "action": "click", "index": 4})
        raise AssertionError(name)

    port = EmbeddedWebChatBrowserPort("chatgpt", execute)
    new_url = await port.start_new_conversation()
    assert new_url == "https://chatgpt.com/"
    assert calls == [
        ("embedded_browser_conversation", {}),
        ("embedded_browser_snapshot", {}),
        ("embedded_browser_click", {"index": 4}),
        ("embedded_browser_conversation", {}),
    ]


@pytest.mark.anyio
async def test_embedded_port_refuses_ambiguous_new_chat_controls():
    calls = []

    async def execute(name, _arguments):
        calls.append(name)
        if name == "embedded_browser_conversation":
            return _tool_result({"ok": True, "provider": "chatgpt",
                "url": "https://chatgpt.com/c/old", "messages": [], "composerReady": True})
        return _tool_result({"ok": True, "elements": [
            {"index": 1, "label": "New chat"},
            {"index": 2, "text": "New chat"},
        ]})

    port = EmbeddedWebChatBrowserPort("chatgpt", execute)
    with pytest.raises(WebChatAdapterError, match="exactly one"):
        await port.start_new_conversation()
    assert calls == ["embedded_browser_conversation", "embedded_browser_snapshot"]


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
        ("embedded_browser_type", {"index": 0, "text": "Perform a harmless test", "append": False}),
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


@pytest.mark.anyio
async def test_embedded_port_chunks_long_prompts_without_losing_order():
    calls = []

    async def execute(name, arguments):
        calls.append((name, arguments))
        if name == "embedded_browser_snapshot":
            return _tool_result({
                "ok": True,
                "elements": [
                    {"index": 0, "editable": True, "disabled": False,
                     "type": "textarea", "placeholder": "Message ChatGPT"},
                ],
            })
        return _tool_result({"ok": True, "action": name})

    prompt = "أ" * 7501
    port = EmbeddedWebChatBrowserPort("chatgpt", execute)
    await port.submit(1, prompt)

    typed = [args for name, args in calls if name == "embedded_browser_type"]
    assert [len(item["text"]) for item in typed] == [3000, 3000, 1501]
    assert "".join(item["text"] for item in typed) == prompt
    assert [item["append"] for item in typed] == [False, True, True]
    assert calls[-1] == ("embedded_browser_press_key", {"key": "Enter"})
