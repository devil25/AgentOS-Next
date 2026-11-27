"""Provider-neutral orchestration for visible, authenticated web-chat tabs.

The transport is intentionally injected. A concrete Chrome adapter must use the
existing paired extension/DevTools bridge and must never read cookies or tokens.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from .protocol import provider_for_url


class WebChatAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssistantMessage:
    message_id: str
    text: str
    complete: bool
    role: str = "assistant"


@dataclass(frozen=True)
class ConversationSnapshot:
    url: str
    title: str
    messages: tuple[AssistantMessage, ...]
    composer_ready: bool
    generating: bool = False
    login_required: bool = False
    blocked_reason: str = ""


class WebChatBrowserPort(Protocol):
    async def inspect(self, tab_id: int | None) -> ConversationSnapshot: ...
    async def submit(self, tab_id: int, text: str) -> None: ...


@dataclass(frozen=True)
class WebChatTurn:
    provider: str
    tab_id: int
    conversation_url: str
    previous_message_id: str
    assistant_message: AssistantMessage


class WebChatSessionAdapter:
    """Send one turn and return only a newly-created, completed assistant reply."""

    def __init__(
        self,
        browser: WebChatBrowserPort,
        *,
        poll_interval: float = 0.75,
        timeout: float = 180.0,
        stable_polls: int = 3,
        empty_generation_grace: float = 8.0,
        on_conversation_url: Callable[[str], None] | None = None,
    ) -> None:
        self.browser = browser
        self.poll_interval = max(0.1, poll_interval)
        # A provider UI that reports "generating" forever must never trap a
        # durable AgentOS turn in a long polling loop.
        self.timeout = max(5.0, min(float(timeout), 60.0))
        self.stable_polls = max(1, stable_polls)
        self.empty_generation_grace = max(0.1, min(float(empty_generation_grace), 15.0))
        self.on_conversation_url = on_conversation_url

    @staticmethod
    def _conversation_identity(url: str) -> tuple[str, str]:
        parsed = urlsplit(str(url or ""))
        return ((parsed.hostname or "").lower(), parsed.path.rstrip("/") or "/")

    async def send_and_wait(
        self, *, tab_id: int | None, prompt: str, expected_provider: str,
        expected_conversation_url: str = "",
    ) -> WebChatTurn:
        if tab_id is not None and (not isinstance(tab_id, int) or isinstance(tab_id, bool) or tab_id < 1):
            raise WebChatAdapterError("A valid Chrome page ID is required.")
        if not str(prompt or "").strip():
            raise WebChatAdapterError("The web-chat prompt cannot be empty.")
        before = await self.browser.inspect(tab_id)
        resolved_page_id = getattr(self.browser, "resolved_page_id", None)
        if tab_id is None:
            tab_id = resolved_page_id
        if not isinstance(tab_id, int) or isinstance(tab_id, bool) or tab_id < 1:
            raise WebChatAdapterError("Chrome did not resolve a valid page ID for the selected provider.")
        provider = provider_for_url(before.url)
        if provider is None or provider != expected_provider:
            raise WebChatAdapterError("The selected tab is not on the expected supported HTTPS provider website.")
        if expected_conversation_url and self._conversation_identity(before.url) != self._conversation_identity(expected_conversation_url):
            raise WebChatAdapterError("The selected Chrome tab is no longer on the conversation bound to this task.")
        if before.login_required:
            raise WebChatAdapterError("Sign in to the provider website in Chrome, then retry.")
        if before.blocked_reason:
            raise WebChatAdapterError(before.blocked_reason)
        if not before.composer_ready:
            raise WebChatAdapterError("The provider message composer is not ready.")
        initial_identity = self._conversation_identity(before.url)
        if self.on_conversation_url is not None:
            self.on_conversation_url(before.url)
        pinned_identity = (
            self._conversation_identity(expected_conversation_url)
            if expected_conversation_url else
            (initial_identity if initial_identity[1] != "/" else None)
        )
        previous_ids = {item.message_id for item in before.messages if item.role == "assistant"}
        previous_id = next(reversed([item.message_id for item in before.messages if item.role == "assistant"]), "")
        await self.browser.submit(tab_id, prompt)

        deadline = asyncio.get_running_loop().time() + self.timeout
        last_candidate: AssistantMessage | None = None
        stable_count = 0
        empty_generation_since: float | None = None
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self.poll_interval)
            current = await self.browser.inspect(tab_id)
            current_provider = provider_for_url(current.url)
            if current_provider != expected_provider:
                raise WebChatAdapterError("The selected tab navigated away from the chosen provider.")
            current_identity = self._conversation_identity(current.url)
            if pinned_identity is None:
                # A provider may create a conversation path after the first
                # prompt from its home page. Pin that newly-created path once.
                if current_identity[1] != "/":
                    pinned_identity = current_identity
                    if self.on_conversation_url is not None:
                        self.on_conversation_url(current.url)
                elif current_identity != initial_identity:
                    raise WebChatAdapterError("The provider conversation changed while AgentOS was waiting; no further action will be sent.")
            elif current_identity != pinned_identity:
                raise WebChatAdapterError("The provider conversation changed while AgentOS was waiting; no further action will be sent.")
            if current.login_required:
                raise WebChatAdapterError("The provider session expired. Sign in again in Chrome.")
            if current.blocked_reason:
                raise WebChatAdapterError(current.blocked_reason)
            now = asyncio.get_running_loop().time()
            if current.generating and not current.messages:
                if empty_generation_since is None:
                    empty_generation_since = now
                elif now - empty_generation_since >= self.empty_generation_grace:
                    raise WebChatAdapterError(
                        "The provider reports generation but exposes no conversation messages. "
                        "The prompt was not confirmed; polling stopped to prevent a loop."
                    )
            else:
                empty_generation_since = None
            candidates = [
                item for item in current.messages
                if item.role == "assistant" and item.message_id not in previous_ids and item.text.strip()
            ]
            if not candidates or current.generating:
                last_candidate = None
                stable_count = 0
                continue
            candidate = candidates[-1]
            if not candidate.complete:
                last_candidate = None
                stable_count = 0
                continue
            if last_candidate and candidate.message_id == last_candidate.message_id and candidate.text == last_candidate.text:
                stable_count += 1
            else:
                last_candidate = candidate
                stable_count = 1
            if stable_count >= self.stable_polls:
                return WebChatTurn(
                    provider=expected_provider,
                    tab_id=tab_id,
                    conversation_url=current.url,
                    previous_message_id=previous_id,
                    assistant_message=candidate,
                )
        raise WebChatAdapterError("Timed out waiting for a new completed assistant response. No action was executed.")


async def inspect_supported_tab(browser: WebChatBrowserPort, tab_id: int) -> dict[str, Any]:
    """Safe preflight metadata; never returns profile, cookies or local storage."""
    snapshot = await browser.inspect(tab_id)
    provider = provider_for_url(snapshot.url)
    return {
        "supported": provider is not None,
        "provider": provider,
        "url": snapshot.url,
        "title": snapshot.title,
        "composer_ready": snapshot.composer_ready,
        "login_required": snapshot.login_required,
        "generating": snapshot.generating,
        "blocked_reason": snapshot.blocked_reason,
    }
