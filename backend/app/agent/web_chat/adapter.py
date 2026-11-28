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


class WebChatContextLimitError(WebChatAdapterError):
    """The provider explicitly requires a fresh conversation."""


_CONTEXT_LIMIT_PATTERNS = (
    "conversation is too long",
    "conversation has reached its limit",
    "context length exceeded",
    "maximum context length",
    "context window is full",
    "message is too long",
    "too many tokens",
    "this chat is too long to continue",
    "you have reached the maximum length for this conversation",
    "المحادثة طويلة جداً",
    "تجاوزت المحادثة الحد",
    "تجاوز حد السياق",
)


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
    context_limit: bool = False


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
        # The embedded browser conversation tool performs its own bounded wait and
        # stability confirmation. Re-reading it here would dispatch identical
        # no-argument transport calls and trip the duplicate-call guard.
        self.stable_polls = 1 if getattr(browser, "provides_stable_snapshots", False) else max(1, stable_polls)
        self.empty_generation_grace = max(0.1, min(float(empty_generation_grace), 15.0))
        self.on_conversation_url = on_conversation_url

    async def start_new_conversation(self) -> str:
        """Start a provider conversation only through the authorized browser port."""
        lock = getattr(self.browser, "operation_lock", None)
        if lock is None:
            return await self._start_new_conversation_unlocked()
        async with lock:
            return await self._start_new_conversation_unlocked()

    async def _start_new_conversation_unlocked(self) -> str:
        starter = getattr(self.browser, "start_new_conversation", None)
        if not callable(starter):
            raise WebChatAdapterError("This browser transport cannot safely start a new provider conversation.")
        result = starter()
        if hasattr(result, "__await__"):
            result = await result
        url = str(result or "")
        if provider_for_url(url) is None:
            raise WebChatAdapterError("The provider did not confirm a fresh conversation URL.")
        return url

    @staticmethod
    def _conversation_identity(url: str) -> tuple[str, str]:
        parsed = urlsplit(str(url or ""))
        hostname = (parsed.hostname or "").lower().rstrip(".")
        # ChatGPT uses both hostnames for the same authenticated product.
        # Treat its legacy and canonical HTTPS hosts as one provider identity;
        # keep the conversation path strict so switching /c/<id> is still blocked.
        if hostname in {"chatgpt.com", "chat.openai.com"}:
            hostname = "chatgpt"
        return (hostname, parsed.path.rstrip("/") or "/")

    async def send_and_wait(
        self, *, tab_id: int | None, prompt: str, expected_provider: str,
        expected_conversation_url: str = "",
    ) -> WebChatTurn:
        # The embedded Electron view is a single shared surface. Serialize the
        # complete inspect/submit/wait cycle across Runs so another Run cannot
        # replace its conversation while this one is waiting for a reply.
        lock = getattr(self.browser, "operation_lock", None)
        if lock is None:
            return await self._send_and_wait_unlocked(
                tab_id=tab_id, prompt=prompt, expected_provider=expected_provider,
                expected_conversation_url=expected_conversation_url,
            )
        async with lock:
            return await self._send_and_wait_unlocked(
                tab_id=tab_id, prompt=prompt, expected_provider=expected_provider,
                expected_conversation_url=expected_conversation_url,
            )

    async def _send_and_wait_unlocked(
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
        if before.context_limit:
            raise WebChatContextLimitError("The provider explicitly reported that this conversation reached its context limit.")
        if not before.composer_ready:
            raise WebChatAdapterError("The provider message composer is not ready.")
        initial_identity = self._conversation_identity(before.url)
        last_persisted_url = before.url
        if self.on_conversation_url is not None:
            self.on_conversation_url(before.url)
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
            if current_identity[0] != initial_identity[0]:
                raise WebChatAdapterError("The selected tab navigated away from the chosen provider.")
            # Concrete conversation routes are authoritative when both sides
            # expose one. If a provider temporarily returns to its SPA root,
            # preserve continuity using message content as well as DOM IDs:
            # accessibility node identities may be recreated during rerender.
            initial_path = initial_identity[1]
            current_path = current_identity[1]
            same_concrete_conversation = (
                initial_path not in {"", "/"}
                and current_path == initial_path
            )
            if (
                initial_path not in {"", "/"}
                and current_path not in {"", "/"}
                and initial_path != current_path
            ):
                raise WebChatAdapterError(
                    "The provider conversation changed while AgentOS was waiting; "
                    "the conversation URL now identifies a different thread."
                )
            current_assistant_messages = [
                item for item in current.messages if item.role == "assistant" and item.text.strip()
            ]
            current_assistant_ids = {item.message_id for item in current_assistant_messages}
            current_assistant_texts = {item.text.strip() for item in current_assistant_messages}
            previous_assistant_texts = {
                item.text.strip() for item in before.messages
                if item.role == "assistant" and item.text.strip()
            }
            has_shared_identity = bool(previous_ids & current_assistant_ids)
            has_shared_content = bool(previous_assistant_texts & current_assistant_texts)
            # The companion deliberately returns a bounded message window. When
            # both snapshots identify the exact same concrete provider thread,
            # older assistant messages may fall outside that window; the stable
            # conversation route is sufficient continuity evidence. If the
            # provider exposes only its SPA root, retain the stricter content/ID
            # overlap requirement because there is no concrete thread identity.
            if previous_ids and not (
                has_shared_identity or has_shared_content or same_concrete_conversation
            ):
                raise WebChatAdapterError(
                    "The provider conversation changed while AgentOS was waiting; "
                    "no prior assistant message content or identity remains."
                )
            if current.url != last_persisted_url and self.on_conversation_url is not None:
                self.on_conversation_url(current.url)
                last_persisted_url = current.url
            if current.login_required:
                raise WebChatAdapterError("The provider session expired. Sign in again in Chrome.")
            if current.blocked_reason:
                raise WebChatAdapterError(current.blocked_reason)
            if current.context_limit:
                raise WebChatContextLimitError("The provider explicitly reported that this conversation reached its context limit.")
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
                if item.role == "assistant"
                and item.message_id not in previous_ids
                and item.text.strip()
                # A provider may recreate DOM identities during a rerender.
                # Do not mistake a re-identified copy of an old reply for the
                # response to the prompt that was just submitted.
                and item.text.strip() not in previous_assistant_texts
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
                lowered = candidate.text.casefold()
                if any(marker in lowered for marker in _CONTEXT_LIMIT_PATTERNS):
                    raise WebChatContextLimitError(
                        "The provider explicitly reported that this conversation reached its context limit."
                    )
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
