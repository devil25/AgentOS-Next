"""Web-chat browser port backed by AgentOS's native embedded Electron view.

All page reads and interactions are dispatched through the active ToolBatchRunner;
this port never attaches to Chrome, reads browser storage, or executes page scripts.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

from .adapter import AssistantMessage, ConversationSnapshot, WebChatAdapterError
from .protocol import provider_for_url


AuthorizedToolExecutor = Callable[[str, dict[str, Any]], Any | Awaitable[Any]]


class EmbeddedWebChatBrowserPort:
    """Provider-neutral conversation port for the single active embedded browser."""

    def __init__(self, provider: str, execute_tool: AuthorizedToolExecutor | None = None) -> None:
        if provider not in {"chatgpt", "gemini", "deepseek", "zai"}:
            raise ValueError("Unsupported embedded web-chat provider.")
        self.provider = provider
        self._execute_tool = execute_tool
        # A stable logical slot, not a Chrome tab ID. Persisted context identifies
        # the provider and conversation URL; the Electron view owns the session.
        self.resolved_page_id: int | None = 1

    def bind_executor(self, execute_tool: AuthorizedToolExecutor) -> None:
        if not callable(execute_tool):
            raise TypeError("An authorized AgentOS tool executor is required.")
        self._execute_tool = execute_tool

    async def _call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._execute_tool is None:
            raise WebChatAdapterError("The embedded browser transport is not bound to the active AgentOS tool runner.")
        result = self._execute_tool(name, arguments or {})
        if hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, dict):
            raise WebChatAdapterError(f"Embedded browser tool {name} returned an invalid result.")
        if result.get("isError") or result.get("is_error"):
            raise WebChatAdapterError(self._result_text(result) or f"Embedded browser tool {name} failed.")
        text = self._result_text(result).strip()
        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as exc:
            structured = result.get("structuredContent") or result.get("structured_content")
            if isinstance(structured, dict):
                payload = structured
            else:
                raise WebChatAdapterError(f"Embedded browser tool {name} returned unreadable data.") from exc
        if not isinstance(payload, dict) or payload.get("ok") is False:
            raise WebChatAdapterError(str((payload or {}).get("error") or f"Embedded browser tool {name} failed.")[:500])
        return payload

    @staticmethod
    def _result_text(result: dict[str, Any]) -> str:
        content = result.get("content") or []
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text") or "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return str(result.get("text") or result.get("message") or "")

    async def inspect(self, tab_id: int | None) -> ConversationSnapshot:
        payload = await self._call("embedded_browser_conversation")
        url = str(payload.get("url") or "")
        detected = provider_for_url(url)
        if detected != self.provider or payload.get("provider") != self.provider:
            raise WebChatAdapterError(
                f"The active embedded page does not match the selected provider ({self.provider})."
            )
        self.resolved_page_id = 1
        messages = tuple(
            AssistantMessage(
                message_id=str(item.get("message_id") or ""),
                text=str(item.get("text") or ""),
                complete=bool(item.get("complete", True)),
                role=str(item.get("role") or ""),
            )
            for item in payload.get("messages", [])
            if isinstance(item, dict) and item.get("message_id") and item.get("text")
        )
        return ConversationSnapshot(
            url=url,
            title=str(payload.get("title") or ""),
            messages=messages,
            composer_ready=bool(payload.get("composerReady")),
            generating=bool(payload.get("generating")),
            login_required=bool(payload.get("loginRequired")),
            blocked_reason=str(payload.get("blockedReason") or ""),
            context_limit=bool(payload.get("contextLimit")),
        )

    async def start_new_conversation(self) -> str:
        """Use the provider's visible New chat control and verify the empty composer."""
        before = await self._call("embedded_browser_conversation")
        before_url = str(before.get("url") or "")
        snapshot = await self._call("embedded_browser_snapshot")
        elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
        pattern = re.compile(
            r"^(?:new chat|new conversation|start new chat|new thread|محادثة جديدة|بدء محادثة)(?:\b|\s|$)",
            re.IGNORECASE,
        )
        matches = []
        for item in elements:
            if not isinstance(item, dict) or item.get("disabled") is True:
                continue
            if str(item.get("tag") or "").lower() != "button" and str(item.get("role") or "").lower() != "button":
                continue
            label = " ".join(str(item.get(key) or "") for key in ("label", "text", "placeholder")).strip()
            if pattern.search(label):
                index = item.get("index")
                if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
                    matches.append(index)
        if len(matches) != 1:
            raise WebChatAdapterError(
                "Could not identify exactly one visible New chat control. No provider navigation was attempted."
            )
        await self._call("embedded_browser_click", {"index": matches[0]})
        deadline = asyncio.get_running_loop().time() + 12.0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            current = await self._call("embedded_browser_conversation")
            url = str(current.get("url") or "")
            messages = current.get("messages") if isinstance(current.get("messages"), list) else []
            if (
                provider_for_url(url) == self.provider
                and bool(current.get("composerReady"))
                and not messages
                and (url != before_url or not before.get("messages"))
            ):
                return url
        raise WebChatAdapterError(
            "The provider New chat control was activated, but a fresh empty conversation was not confirmed."
        )

    async def submit(self, tab_id: int, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise WebChatAdapterError("The web-chat prompt cannot be empty.")
        snapshot = await self._call("embedded_browser_snapshot")
        elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
        candidates = [
            item for item in elements
            if isinstance(item, dict)
            and item.get("editable") is True
            and item.get("disabled") is not True
            and str(item.get("type") or "").lower() != "password"
        ]
        preferred = [
            item for item in candidates
            if any(word in " ".join(str(item.get(key) or "") for key in ("role", "label", "placeholder")).lower()
                   for word in ("message", "prompt", "ask", "send"))
        ]
        choices = preferred or candidates
        if len(choices) != 1:
            raise WebChatAdapterError(
                "Could not identify exactly one editable composer in the embedded provider. "
                "No text was entered; inspect the embedded browser and provider layout."
            )
        index = choices[0].get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise WebChatAdapterError("The embedded provider composer index is invalid.")
        # The bridge accepts bounded chunks so large provider prompts do not
        # violate tool schemas or the loopback HTTP request-size limit.
        chunk_size = 3000
        chunks = [text[offset:offset + chunk_size] for offset in range(0, len(text), chunk_size)]
        for chunk_index, chunk in enumerate(chunks):
            await self._call("embedded_browser_type", {
                "index": index, "text": chunk, "append": chunk_index > 0,
            })
        await self._call("embedded_browser_press_key", {"key": "Enter"})
