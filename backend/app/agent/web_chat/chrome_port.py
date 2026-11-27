"""Chrome web-chat adapter over existing, policy-gated browser tools.

No arbitrary page JavaScript is accepted here. Inspection uses the fixed
read-only inspect_web_chat capability; message submission uses the normal
snapshot/fill/press_key tools, so their existing authorization and journaling
remain in force.
"""
from __future__ import annotations

import inspect
import json
import re
from typing import Any, Awaitable, Protocol

from .adapter import AssistantMessage, ConversationSnapshot, WebChatAdapterError
from .protocol import provider_for_url


class AuthorizedBrowserToolExecutor(Protocol):
    """Dispatch a registered Chrome DevTools tool through the active ToolBatchRunner."""

    def __call__(self, tool_name: str, arguments: dict[str, Any]) -> Any | Awaitable[Any]: ...


class ChromeWebChatBrowserPort:
    """Visible-page operations backed by the existing Chrome DevTools tool path.

    page_id is the explicit stable pageId from list_pages (not a Chrome tab index).
    The caller must bind it to the task and pass an executor that routes every
    call through ToolBatchRunner.
    """

    _PREFIX = "mcp__chrome-devtools__"

    def __init__(self, execute_tool: AuthorizedBrowserToolExecutor | None = None, *, provider: str | None = None) -> None:
        if execute_tool is not None and not callable(execute_tool):
            raise TypeError("The Chrome browser-tool executor must be callable.")
        self._execute_tool = execute_tool
        self.provider = provider
        self.resolved_page_id: int | None = None

    def bind_executor(self, execute_tool: AuthorizedBrowserToolExecutor) -> None:
        """Bind the active run's ToolBatchRunner transport after runtime setup."""
        if not callable(execute_tool):
            raise TypeError("An authorized Chrome browser-tool executor is required.")
        self._execute_tool = execute_tool

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._execute_tool is None:
            raise WebChatAdapterError("The web-chat browser transport is not bound to an active AgentOS tool runner.")
        result = self._execute_tool(self._PREFIX + name, arguments)
        if inspect.isawaitable(result):
            result = await result
        is_error = (
            bool(result.get("isError") or result.get("is_error"))
            if isinstance(result, dict)
            else bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
        )
        if is_error:
            raise WebChatAdapterError(self._result_text(result) or f"Chrome tool {name} failed.")
        structured = (
            result.get("structuredContent") or result.get("structured_content")
            if isinstance(result, dict)
            else getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
        )
        return structured if structured else result

    @staticmethod
    def _result_text(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            content = result.get("content") or []
            if isinstance(content, list):
                return "\n".join(
                    str(item.get("text") or "") for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            return str(result.get("text") or result.get("message") or "")
        content = getattr(result, "content", None)
        if isinstance(content, list):
            return "\n".join(str(getattr(item, "text", "") or "") for item in content)
        return str(getattr(result, "text", "") or result or "")

    @classmethod
    def _decode_json(cls, result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            if "url" in result or "messages" in result:
                return result
            structured = result.get("structuredContent") or result.get("structured_content")
            if isinstance(structured, dict):
                return structured
        text = cls._result_text(result).strip()
        try:
            value = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise WebChatAdapterError("Chrome returned an unreadable web-chat inspection result.") from exc
        if not isinstance(value, dict):
            raise WebChatAdapterError("Chrome returned an invalid web-chat inspection result.")
        return value

    async def _resolve_page_id(self) -> int:
        if self.provider is None:
            raise WebChatAdapterError("A provider is required for automatic Chrome page discovery.")
        result = await self._call("list_pages", {})
        pages_text = self._result_text(result)
        matches: list[tuple[int, str, str]] = []
        for line in pages_text.splitlines():
            match = re.match(r"^\s*(\d+):\s*(.*?)\s*$", line)
            if not match:
                continue
            page_id = int(match.group(1))
            url_match = re.search(r"https?://[^\s)]+", match.group(2))
            if not url_match:
                continue
            url = url_match.group(0).rstrip(".,;")
            if provider_for_url(url) == self.provider:
                matches.append((page_id, match.group(2), url))
        if not matches:
            raise WebChatAdapterError(
                f"No open Chrome tab was found for {self.provider}. Open its website in Chrome and sign in, then retry."
            )
        if len(matches) > 1:
            choices = "; ".join(f"Page {page_id}: {title}" for page_id, title, _url in matches[:8])
            raise WebChatAdapterError(
                f"More than one {self.provider} tab is open. Close the tabs you do not want and retry, or choose one explicitly. {choices}"
            )
        self.resolved_page_id = matches[0][0]
        return self.resolved_page_id

    async def inspect(self, tab_id: int | None) -> ConversationSnapshot:
        if tab_id is None:
            tab_id = await self._resolve_page_id()
        self.resolved_page_id = tab_id
        value = self._decode_json(await self._call("inspect_web_chat", {"pageId": tab_id}))
        url = str(value.get("url") or "")
        if provider_for_url(url) is None:
            raise WebChatAdapterError(str(value.get("blockedReason") or "The selected tab is not a supported HTTPS provider."))
        messages = tuple(
            AssistantMessage(
                message_id=str(item.get("message_id") or ""),
                text=str(item.get("text") or ""),
                complete=bool(item.get("complete")),
                role=str(item.get("role") or "assistant"),
            )
            for item in value.get("messages", [])
            if isinstance(item, dict) and item.get("message_id") and item.get("text")
        )
        return ConversationSnapshot(
            url=url, title=str(value.get("title") or ""), messages=messages,
            composer_ready=bool(value.get("composerReady")),
            generating=bool(value.get("generating")),
            login_required=bool(value.get("loginRequired")),
            blocked_reason=str(value.get("blockedReason") or ""),
        )

    async def submit(self, tab_id: int, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise WebChatAdapterError("The web-chat prompt cannot be empty.")
        snapshot = self._result_text(await self._call("take_snapshot", {"pageId": tab_id}))
        candidates = []
        for line in snapshot.splitlines():
            match = re.match(r'^\s*uid=([^\s]+)\s+textbox\s+"([^"]*)"', line)
            if match:
                candidates.append((match.group(1), match.group(2).lower()))
        preferred = [item for item in candidates if any(
            word in item[1] for word in ("message", "prompt", "ask", "send")
        )]
        choices = preferred or candidates
        if len(choices) != 1:
            raise WebChatAdapterError(
                "Could not identify exactly one accessible web-chat composer. "
                "No text was entered; inspect the page snapshot and provider layout."
            )
        uid = choices[0][0]
        await self._call("fill", {"pageId": tab_id, "uid": uid, "value": text})
        result = await self._call("press_key", {"pageId": tab_id, "key": "Enter"})
        if "error" in self._result_text(result).lower() or "blocked" in self._result_text(result).lower():
            raise WebChatAdapterError("Chrome did not confirm the web-chat send action.")
