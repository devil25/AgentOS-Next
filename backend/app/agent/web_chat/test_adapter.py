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
             composer=True, login=False, blocked="", context_limit=False):
    return ConversationSnapshot(
        url=url, title="ChatGPT", messages=tuple(messages),
        composer_ready=composer, generating=generating,
        login_required=login, blocked_reason=blocked, context_limit=context_limit,
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


def test_provider_wait_has_no_default_deadline_or_empty_generation_cutoff():
    adapter = WebChatSessionAdapter(FakeBrowser([]))
    assert adapter.timeout is None
    assert adapter.empty_generation_grace is None


@pytest.mark.anyio
async def test_turn_does_not_return_partial_or_generating_response():
    old = AssistantMessage("old", "Earlier", True)
    partial = AssistantMessage("new", "partial", False)
    browser = FakeBrowser([snapshot([old]), snapshot([old, partial], generating=True)])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)
    with pytest.raises(WebChatAdapterError, match="Timed out"):
        await adapter.send_and_wait(tab_id=12, prompt="Do work", expected_provider="chatgpt")


@pytest.mark.anyio
async def test_turn_stops_polling_when_provider_claims_generation_without_any_messages():
    browser = FakeBrowser([
        snapshot([], generating=True),
        snapshot([], generating=True),
        snapshot([], generating=True),
        snapshot([], generating=True),
    ])
    adapter = WebChatSessionAdapter(
        browser, poll_interval=0.1, timeout=5, empty_generation_grace=0.2,
    )
    with pytest.raises(WebChatAdapterError, match="no conversation messages"):
        await adapter.send_and_wait(
            tab_id=12, prompt="Do work", expected_provider="chatgpt",
        )
    assert browser.submitted == [(12, "Do work")]


@pytest.mark.anyio
async def test_turn_rolls_over_when_provider_exposes_an_explicit_context_limit_banner():
    from app.agent.web_chat.adapter import WebChatContextLimitError

    browser = FakeBrowser([snapshot([AssistantMessage("old", "Prior reply", True)], context_limit=True)])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)
    with pytest.raises(WebChatContextLimitError, match="context limit"):
        await adapter.send_and_wait(tab_id=12, prompt="Continue", expected_provider="chatgpt")
    assert browser.submitted == []


@pytest.mark.anyio
async def test_turn_classifies_explicit_provider_context_limit_without_retrying():
    from app.agent.web_chat.adapter import WebChatContextLimitError

    old = AssistantMessage("old", "Earlier response", True)
    limit = AssistantMessage("limit", "This conversation is too long. Start a new chat.", True)
    browser = FakeBrowser([
        snapshot([old]),
        snapshot([old, limit]),
        snapshot([old, limit]),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5, stable_polls=2)
    with pytest.raises(WebChatContextLimitError, match="context limit"):
        await adapter.send_and_wait(tab_id=12, prompt="Continue", expected_provider="chatgpt")
    assert browser.submitted == [(12, "Continue")]


@pytest.mark.anyio
async def test_turn_allows_chatgpt_home_url_to_become_new_conversation_after_submit():
    new = AssistantMessage("new", "The requested response", True)
    browser = FakeBrowser([
        snapshot([], url="https://chatgpt.com/"),
        snapshot([new], url="https://chatgpt.com/c/newly-created"),
        snapshot([new], url="https://chatgpt.com/c/newly-created"),
    ])
    persisted = []
    adapter = WebChatSessionAdapter(
        browser, poll_interval=0.1, timeout=5, stable_polls=2,
        on_conversation_url=persisted.append,
    )
    turn = await adapter.send_and_wait(
        tab_id=12, prompt="Say hello", expected_provider="chatgpt",
        expected_conversation_url="https://chatgpt.com/",
    )
    assert turn.assistant_message.text == "The requested response"
    assert turn.conversation_url == "https://chatgpt.com/c/newly-created"
    assert persisted == [
        "https://chatgpt.com/",
        "https://chatgpt.com/c/newly-created",
    ]


@pytest.mark.anyio
async def test_turn_rejects_conversation_switch_after_submission():
    old = AssistantMessage("old", "Earlier response", True)
    different = AssistantMessage("other-chat-message", "A different conversation", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chatgpt.com/c/task-a"),
        snapshot([different], url="https://chatgpt.com/c/task-b"),
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
async def test_turn_allows_chatgpt_legacy_and_canonical_hosts_for_same_conversation():
    old = AssistantMessage("old", "Earlier response", True)
    new = AssistantMessage("new", "Completed response", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chat.openai.com/c/task-a"),
        snapshot([old, new], url="https://chatgpt.com/c/task-a"),
        snapshot([old, new], url="https://chatgpt.com/c/task-a"),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5, stable_polls=2)
    turn = await adapter.send_and_wait(
        tab_id=12, prompt="Continue", expected_provider="chatgpt",
        expected_conversation_url="https://chat.openai.com/c/task-a",
    )
    assert turn.assistant_message.text == "Completed response"


@pytest.mark.anyio
async def test_turn_still_rejects_different_chatgpt_conversation_across_host_aliases():
    old = AssistantMessage("old", "Earlier response", True)
    different = AssistantMessage("new-document-node", "A different conversation", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chat.openai.com/c/task-a"),
        snapshot([different], url="https://chatgpt.com/c/task-b"),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)
    with pytest.raises(WebChatAdapterError, match="conversation changed"):
        await adapter.send_and_wait(
            tab_id=12, prompt="Continue", expected_provider="chatgpt",
            expected_conversation_url="https://chat.openai.com/c/task-a",
        )


@pytest.mark.anyio
async def test_initial_turn_rejects_switch_from_existing_conversation():
    old = AssistantMessage("old", "Earlier response", True)
    different = AssistantMessage("other-chat-message", "A different conversation", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chatgpt.com/c/task-a"),
        snapshot([different], url="https://chatgpt.com/c/task-b"),
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


@pytest.mark.anyio
async def test_turn_accepts_an_automatically_resolved_page_id():
    old = AssistantMessage("old", "Earlier response", True)
    new = AssistantMessage("new", "Completed", True)

    class AutoResolvedBrowser(FakeBrowser):
        resolved_page_id = 23

    browser = AutoResolvedBrowser([
        snapshot([old]), snapshot([old, new]), snapshot([old, new]),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5, stable_polls=2)
    turn = await adapter.send_and_wait(
        tab_id=None, prompt="Use the detected tab", expected_provider="chatgpt",
    )
    assert turn.tab_id == 23
    assert browser.submitted == [(23, "Use the detected tab")]


@pytest.mark.anyio
async def test_turn_allows_same_accessibility_history_when_provider_spa_url_changes():
    old = AssistantMessage("stable-backend-node-101", "Earlier response", True)
    new = AssistantMessage("stable-backend-node-202", "New response", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chatgpt.com/c/task-a"),
        snapshot([old, new], url="https://chatgpt.com/"),
        snapshot([old, new], url="https://chatgpt.com/"),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5, stable_polls=2)
    turn = await adapter.send_and_wait(
        tab_id=12, prompt="Continue", expected_provider="chatgpt",
        expected_conversation_url="https://chatgpt.com/c/task-a",
    )
    assert turn.assistant_message.message_id == "stable-backend-node-202"


@pytest.mark.anyio
async def test_turn_accepts_dom_identity_refresh_when_prior_assistant_content_is_preserved():
    old_before = AssistantMessage("dom-node-before", "Earlier response remains in this thread", True)
    old_after = AssistantMessage("dom-node-after", "Earlier response remains in this thread", True)
    new = AssistantMessage("new-response-node", "The new completed response", True)
    browser = FakeBrowser([
        snapshot([old_before], url="https://chatgpt.com/c/task-a"),
        snapshot([old_after, new], url="https://chatgpt.com/"),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)

    turn = await adapter.send_and_wait(
        tab_id=12, prompt="Continue this task", expected_provider="chatgpt",
        expected_conversation_url="https://chatgpt.com/c/task-a",
    )

    assert turn.assistant_message.text == "The new completed response"


@pytest.mark.anyio
async def test_turn_accepts_bounded_snapshot_when_concrete_conversation_route_is_unchanged():
    old = AssistantMessage("old-dom-node", "Earlier assistant reply outside the capture window", True)
    new = AssistantMessage("new-dom-node", "DEVICE-EXEC-01 action response", True)
    thread_url = "https://chatgpt.com/c/task-bounded-window"
    browser = FakeBrowser([
        snapshot([old], url=thread_url),
        # Companion snapshots are bounded; the previous assistant message can
        # disappear from the window even though this is still the same thread.
        snapshot([new], url=thread_url),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)

    turn = await adapter.send_and_wait(
        tab_id=12, prompt="Run the local device test", expected_provider="chatgpt",
        expected_conversation_url=thread_url,
    )

    assert turn.assistant_message.message_id == "new-dom-node"
    assert turn.assistant_message.text == "DEVICE-EXEC-01 action response"
    assert browser.submitted == [(12, "Run the local device test")]


@pytest.mark.anyio
async def test_turn_does_not_treat_rerendered_old_reply_as_new_response():
    old_before = AssistantMessage("old-dom-node", "Historical answer", True)
    old_after = AssistantMessage("new-dom-node", "Historical answer", True)
    browser = FakeBrowser([
        snapshot([old_before]),
        snapshot([old_after]),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)

    with pytest.raises(WebChatAdapterError, match="Timed out"):
        await adapter.send_and_wait(
            tab_id=12, prompt="Do a new task", expected_provider="chatgpt",
        )


@pytest.mark.anyio
async def test_turn_rejects_different_conversation_even_when_old_text_is_repeated():
    old = AssistantMessage("old-node", "A repeated generic response", True)
    same_text = AssistantMessage("new-thread-node", "A repeated generic response", True)
    browser = FakeBrowser([
        snapshot([old], url="https://chatgpt.com/c/task-a"),
        snapshot([same_text], url="https://chatgpt.com/c/task-b"),
    ])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5)

    with pytest.raises(WebChatAdapterError, match="conversation changed"):
        await adapter.send_and_wait(
            tab_id=12, prompt="Continue", expected_provider="chatgpt",
            expected_conversation_url="https://chatgpt.com/c/task-a",
        )


def test_embedded_browser_ports_share_one_run_isolation_lock():
    from app.agent.web_chat.embedded_port import EmbeddedWebChatBrowserPort

    first = EmbeddedWebChatBrowserPort("chatgpt")
    second = EmbeddedWebChatBrowserPort("gemini")

    assert first.operation_lock is second.operation_lock


@pytest.mark.anyio
async def test_embedded_stable_snapshot_browser_uses_one_post_submit_read():
    class StableSnapshotBrowser(FakeBrowser):
        provides_stable_snapshots = True

        def __init__(self, snapshots):
            super().__init__(snapshots)
            self.inspect_calls = 0

        async def inspect(self, tab_id):
            self.inspect_calls += 1
            return await super().inspect(tab_id)

    old = AssistantMessage("old", "Earlier response", True)
    new = AssistantMessage("new", "A completed response", True)
    browser = StableSnapshotBrowser([snapshot([old]), snapshot([old, new])])
    adapter = WebChatSessionAdapter(browser, poll_interval=0.1, timeout=5, stable_polls=3)

    turn = await adapter.send_and_wait(tab_id=12, prompt="Do work", expected_provider="chatgpt")

    assert turn.assistant_message.message_id == "new"
    assert browser.submitted == [(12, "Do work")]
    assert browser.inspect_calls == 2
