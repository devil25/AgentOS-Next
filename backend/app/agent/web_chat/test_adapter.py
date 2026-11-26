import pytest

from app.agent.web_chat.adapter import (
    AssistantMessage,
    ConversationSnapshot,
    WebChatAdapterError,
    WebChatSessionAdapter,
)


class FakeBrowser:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.submitted = []

    async def inspect(self, tab_id):
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    async def submit(self, tab_id, text):
        self.submitted.append((tab_id, text))


def snapshot(messages=(), *, url="https://chatgpt.com/", generating=False,
             composer=True, login=False, blocked=""):
    return ConversationSnapshot(
        url=url, title="ChatGPT", messages=tuple(messages),
        composer_ready=composer, generating=generating,
        login_required=login, blocked_reason=blocked,
    )


@pytest.mark.anyio
async def test_turn_returns_only_new_completed_assistant_message_after_stable_reads():
    old = AssistantMessage("old", "Earlier response", True)
    new = AssistantMessage("new", "A completed response", True)
    browser = FakeBrowser([
        snapshot([old]), snapshot([old, new]), snapshot([old, new]),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5, stable_polls=2)
    turn = await adapter.send_and_wait(tab_id=12, prompt="Do work", expected_provider="chatgpt")
    assert turn.assistant_message.message_id == "new"
    assert turn.assistant_message.text == new.text
    assert browser.submitted == [(12, "Do work")]


@pytest.mark.anyio
async def test_turn_rejects_wrong_site_and_never_submits():
    browser = FakeBrowser([snapshot(url="https://chat.z.ai/")])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)
    with pytest.raises(WebChatAdapterError):
        await adapter.send_and_wait(tab_id=12, prompt="Do work", expected_provider="chatgpt")
    assert browser.submitted == []


@pytest.mark.anyio
async def test_turn_rejects_login_required_and_unready_composer():
    for state in (snapshot(login=True), snapshot(composer=False)):
        browser = FakeBrowser([state])
        adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)
        with pytest.raises(WebChatAdapterError):
            await adapter.send_and_wait(tab_id=12, prompt="Do work", expected_provider="chatgpt")
        assert browser.submitted == []


@pytest.mark.anyio
async def test_turn_does_not_return_partial_or_generating_response():
    old = AssistantMessage("old", "Earlier", True)
    partial = AssistantMessage("new", "partial", False)
    browser = FakeBrowser([snapshot([old]), snapshot([old, partial], generating=True)])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)
    with pytest.raises(WebChatAdapterError, match="Timed out"):
        await adapter.send_and_wait(tab_id=12, prompt="Do work", expected_provider="chatgpt")


@pytest.mark.anyio
async def test_turn_rejects_conversation_switch_after_submission():
    old = AssistantMessage("old", "Earlier response", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chatgpt.com/c/task-a"),
        snapshot([old], url="https://chatgpt.com/c/task-b"),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)
    with pytest.raises(WebChatAdapterError, match="conversation changed"):
        await adapter.send_and_wait(
            tab_id=12, prompt="Continue task", expected_provider="chatgpt",
            expected_conversation_url="https://chatgpt.com/c/task-a",
        )
    assert browser.submitted == [(12, "Continue task")]


@pytest.mark.anyio
async def test_turn_allows_provider_query_and_fragment_changes_for_same_conversation():
    old = AssistantMessage("old", "Earlier response", True)
    new = AssistantMessage("new", "Completed", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chatgpt.com/c/task-a?model=x"),
        snapshot([old, new], url="https://chatgpt.com/c/task-a?model=y#response"),
        snapshot([old, new], url="https://chatgpt.com/c/task-a?model=y#response"),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5, stable_polls=2)
    turn = await adapter.send_and_wait(
        tab_id=12, prompt="Continue task", expected_provider="chatgpt",
        expected_conversation_url="https://chatgpt.com/c/task-a",
    )
    assert turn.assistant_message.message_id == "new"


@pytest.mark.anyio
async def test_initial_turn_rejects_switch_from_existing_conversation():
    old = AssistantMessage("old", "Earlier response", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chatgpt.com/c/task-a"),
        snapshot([old], url="https://chatgpt.com/c/task-b"),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)
    with pytest.raises(WebChatAdapterError, match="conversation changed"):
        await adapter.send_and_wait(tab_id=12, prompt="Do work", expected_provider="chatgpt")



@pytest.mark.anyio
async def test_adapter_persists_initial_and_newly_created_conversation_urls():
    new = AssistantMessage("new", "Ready", True)
    browser = FakeBrowser([
        snapshot([], url="https://chatgpt.com/"),
        snapshot([new], url="https://chatgpt.com/c/created"),
        snapshot([new], url="https://chatgpt.com/c/created"),
    ])
    persisted = []
    adapter = WebChatSessionAdapter(
        browser, poll_interval=0.1, timeout=5, stable_polls=2,
        on_conversation_url=persisted.append,
    )
    turn = await adapter.send_and_wait(
        tab_id=12, prompt="Start task", expected_provider="chatgpt",
    )
    assert turn.conversation_url == "https://chatgpt.com/c/created"
    assert persisted == [
        "https://chatgpt.com/",
        "https://chatgpt.com/c/created",
    ]
