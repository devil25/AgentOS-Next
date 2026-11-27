import pytest

from app.agent.web_chat.chrome_port import ChromeWebChatBrowserPort
from app.agent.web_chat.adapter import WebChatAdapterError


class FakeAuthorizedToolExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def __call__(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.results.pop(0)


@pytest.mark.anyio
async def test_inspect_uses_fixed_read_only_browser_tool():
    executor = FakeAuthorizedToolExecutor([{
        "url": "https://chatgpt.com/c/example",
        "title": "ChatGPT",
        "provider": "chatgpt",
        "messages": [{"message_id": "m1", "text": "Hello", "complete": True, "role": "assistant"}],
        "composerReady": True,
        "generating": False,
        "loginRequired": False,
        "blockedReason": "",
    }])
    port = ChromeWebChatBrowserPort(executor)
    snapshot = await port.inspect(7)
    assert snapshot.url == "https://chatgpt.com/c/example"
    assert snapshot.messages[0].text == "Hello"
    assert snapshot.composer_ready is True
    assert executor.calls == [("mcp__chrome-devtools__inspect_web_chat", {"pageId": 7})]


@pytest.mark.anyio
async def test_submit_uses_snapshot_fill_and_enter_tools_without_page_script():
    executor = FakeAuthorizedToolExecutor([
        {"content": [{"type": "text", "text": '## Latest page snapshot\nuid=2_1 RootWebArea "ChatGPT"\n  uid=2_4 textbox "Message ChatGPT"'}]},
        {"content": [{"type": "text", "text": "Successfully filled out the element"}]},
        {"content": [{"type": "text", "text": "Successfully pressed key: Enter"}]},
    ])
    port = ChromeWebChatBrowserPort(executor)
    prompt = 'Please type this safely: "); alert(1); //'
    await port.submit(7, prompt)
    assert executor.calls == [
        ("mcp__chrome-devtools__take_snapshot", {"pageId": 7}),
        ("mcp__chrome-devtools__fill", {"pageId": 7, "uid": "2_4", "value": prompt}),
        ("mcp__chrome-devtools__press_key", {"pageId": 7, "key": "Enter"}),
    ]
    assert all("evaluate_script" not in name for name, _ in executor.calls)


@pytest.mark.anyio
async def test_submit_refuses_ambiguous_composer_without_typing():
    executor = FakeAuthorizedToolExecutor([{
        "content": [{"type": "text", "text": 'uid=1_1 RootWebArea\nuid=1_2 textbox "Search"\nuid=1_3 textbox "Input"'}]
    }])
    port = ChromeWebChatBrowserPort(executor)
    with pytest.raises(WebChatAdapterError, match="exactly one accessible"):
        await port.submit(7, "Do not send")
    assert len(executor.calls) == 1


@pytest.mark.anyio
async def test_inspect_rejects_unsupported_origin():
    executor = FakeAuthorizedToolExecutor([{
        "url": "https://example.com/",
        "title": "Example",
        "messages": [],
        "composerReady": True,
    }])
    port = ChromeWebChatBrowserPort(executor)
    with pytest.raises(WebChatAdapterError, match="supported HTTPS provider"):
        await port.inspect(7)

def test_fixed_web_chat_inspection_is_read_only_and_never_reads_credentials():
    from app.skills.browser_use.extension.web_chat_inspection import WEB_CHAT_INSPECTION

    for host in ("chatgpt.com", "gemini.google.com", "chat.deepseek.com", "chat.z.ai"):
        assert host in WEB_CHAT_INSPECTION
    assert "querySelectorAll(selector)" in WEB_CHAT_INSPECTION
    assert "data-message-id" in WEB_CHAT_INSPECTION
    assert "data-turn-id" in WEB_CHAT_INSPECTION
    assert "document.cookie" not in WEB_CHAT_INSPECTION
    assert "localStorage" not in WEB_CHAT_INSPECTION
    assert "sessionStorage" not in WEB_CHAT_INSPECTION
    assert "eval(" not in WEB_CHAT_INSPECTION


@pytest.mark.anyio
async def test_automatic_discovery_selects_the_only_matching_provider_tab():
    executor = FakeAuthorizedToolExecutor([
        {"content": [{"type": "text", "text": "## Pages\n1: Gmail (https://mail.google.com/)\n4: ChatGPT (https://chatgpt.com/c/example) [selected]"}]},
        {
            "url": "https://chatgpt.com/c/example",
            "title": "ChatGPT",
            "messages": [],
            "composerReady": True,
            "generating": False,
            "loginRequired": False,
            "blockedReason": "",
        },
    ])
    port = ChromeWebChatBrowserPort(executor, provider="chatgpt")
    snapshot = await port.inspect(None)
    assert snapshot.url == "https://chatgpt.com/c/example"
    assert port.resolved_page_id == 4
    assert executor.calls == [
        ("mcp__chrome-devtools__list_pages", {}),
        ("mcp__chrome-devtools__inspect_web_chat", {"pageId": 4}),
    ]


@pytest.mark.anyio
async def test_automatic_discovery_refuses_ambiguous_provider_tabs():
    executor = FakeAuthorizedToolExecutor([{
        "content": [{"type": "text", "text": "## Pages\n2: ChatGPT one (https://chatgpt.com/c/one)\n5: ChatGPT two (https://chatgpt.com/c/two)"}]
    }])
    port = ChromeWebChatBrowserPort(executor, provider="chatgpt")
    with pytest.raises(WebChatAdapterError, match="More than one chatgpt tab"):
        await port.inspect(None)
    assert len(executor.calls) == 1
