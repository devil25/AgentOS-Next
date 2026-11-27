"""LLMClient-compatible adapter that uses a signed-in web-chat UI.

Tool calls are returned to AgentOS TurnLoop as normal ToolCallRequests, so the
existing ToolBatchRunner, execution gate, approvals, journaling and verification
remain the only path that can execute tools.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from .adapter import WebChatContextLimitError, WebChatSessionAdapter
from .protocol import action_instruction, build_provider_turn, parse_web_chat_reply


WEB_CHAT_TOOL_CATALOG_MAX_BYTES = 40_000
_WEB_CHAT_TOOL_DESCRIPTION_MAX_CHARS = 480


def _web_chat_tokens(value: str) -> set[str]:
    import re

    return {token for token in re.findall(r"[a-z0-9_]+|[\u0600-\u06ff]+", str(value or "").casefold()) if len(token) > 1}


def _compact_json_schema(value: Any, *, depth: int = 0) -> Any:
    """Keep validation-relevant JSON Schema fields while dropping verbose metadata."""
    if depth > 8:
        return {}
    if isinstance(value, list):
        return [_compact_json_schema(item, depth=depth + 1) for item in value[:80]]
    if not isinstance(value, dict):
        return value
    allowed = {
        "type", "properties", "required", "items", "enum", "const", "anyOf", "oneOf",
        "allOf", "additionalProperties", "$ref", "$defs", "definitions", "minItems",
        "maxItems", "minLength", "maxLength", "minimum", "maximum", "pattern",
        "format", "nullable",
    }
    compact = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if key == "properties" and isinstance(item, dict):
            compact[key] = {
                str(name): _compact_json_schema(schema, depth=depth + 1)
                for name, schema in list(item.items())[:100]
            }
        else:
            compact[key] = _compact_json_schema(item, depth=depth + 1)
    return compact


def _select_web_chat_tool_catalog(tools: list[dict], goal: str) -> list[dict]:
    """Build a task-relevant, size-bounded catalog without changing runtime permissions."""
    goal_tokens = _web_chat_tokens(goal)
    candidates = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        name = str(tool.get("name") or "")
        description = str(tool.get("description") or "")
        parameters = tool.get("parameters") or {"type": "object", "properties": {}}
        property_names = " ".join((parameters.get("properties") or {}).keys()) if isinstance(parameters, dict) else ""
        name_tokens = _web_chat_tokens(name.replace("_", " ").replace(".", " ").replace("-", " "))
        description_tokens = _web_chat_tokens(description)
        property_tokens = _web_chat_tokens(property_names)
        score = 8 * len(goal_tokens & name_tokens) + 3 * len(goal_tokens & property_tokens) + len(goal_tokens & description_tokens)
        entry = {
            "name": name,
            "description": description[:_WEB_CHAT_TOOL_DESCRIPTION_MAX_CHARS],
            "parameters": _compact_json_schema(parameters),
        }
        candidates.append((score, index, entry))

    # Preserve original order among equally relevant tools. A small baseline set
    # keeps generic requests actionable; the rest are admitted only when relevant.
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = []
    used = 2  # JSON array brackets plus a small safety margin.
    for score, _index, entry in candidates:
        if score == 0 and len(selected) >= 8:
            continue
        encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8")) + (1 if selected else 0)
        if used + size > WEB_CHAT_TOOL_CATALOG_MAX_BYTES:
            continue
        selected.append(entry)
        used += size
    return selected


class WebChatLLMClient:
    def __init__(
        self,
        adapter: WebChatSessionAdapter,
        *,
        provider: str,
        tab_id: int | None,
        model_name: str | None = None,
        run_id: str = "",
        conversation_url: str = "",
    ) -> None:
        self.provider = "web_chat"
        self.web_chat_provider = provider
        self.model_name = model_name or f"{provider.title()} Web Chat"
        self.api_key = ""
        self.base_url = ""
        self.reasoning_effort = "provider_default"
        self.supports_vision = False
        self.adapter = adapter
        self.tab_id = int(tab_id) if tab_id is not None else None
        self._last_action_id = ""
        self._conversation_url = str(conversation_url or "")
        self.run_id = str(run_id or "")
        self._seen_action_ids: set[str] = set()
        self._call_lock = asyncio.Lock()

    def bind_durable_run(self, durable_run) -> None:
        """Persist the selected website/page as soon as the durable Run exists."""
        if not self.run_id or self.tab_id is None:
            return
        durable_run_id = str(getattr(durable_run, "run_id", "") or "")
        if durable_run_id != self.run_id:
            raise RuntimeError("The web-chat target does not match the active durable Run.")
        from .session_store import save_web_chat_run_context
        save_web_chat_run_context(
            self.run_id, self.web_chat_provider, self.tab_id,
            conversation_url=self._conversation_url,
        )

    def bind_browser_tool_executor(self, execute_tool) -> None:
        """Attach the per-turn authorized dispatcher supplied by TurnLoop."""
        browser = getattr(self.adapter, "browser", None)
        if browser is None:
            # Lightweight test adapters and non-Chrome transports may not expose
            # a browser port. Production Chrome adapters always do.
            return
        binder = getattr(browser, "bind_executor", None)
        if not callable(binder):
            raise RuntimeError("The web-chat browser port cannot bind the active AgentOS tool runner.")
        binder(execute_tool)

    @staticmethod
    def build_provider_tool_result_message(result) -> dict:
        return {
            "role": "user",
            "content": f"AgentOS tool result for {result.name}: {result.output}",
            "tool_result": True,
            "web_chat_action_result": True,
        }

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str = "",
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        tool_choice: str | dict | None = None,
        reasoning_callback: Callable[[str], Awaitable[None]] | None = None,
    ):
        from app.agent.llm_client import LLMResponse, ToolCallRequest

        async with self._call_lock:
            latest_result = next(
                (item for item in reversed(messages)
                 if isinstance(item, dict) and item.get("web_chat_action_result") is True),
                None,
            )
            latest_user = next(
                (item for item in reversed(messages)
                 if isinstance(item, dict) and item.get("role") == "user" and not item.get("tool_result")),
                None,
            )
            goal = str((latest_user or {}).get("content") or "").strip()
            catalog = _select_web_chat_tool_catalog(tools, goal) if tools and goal else []
            if latest_result is not None:
                result_text = str(latest_result.get("content") or "")
                prompt = (
                    "Continue the same task using this actual AgentOS execution result:\n"
                    + result_text + "\n\n" + action_instruction()
                )
            else:
                if not goal:
                    raise RuntimeError("The web-chat provider did not receive a user task.")
                catalog_json = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
                if len(catalog_json.encode("utf-8")) > WEB_CHAT_TOOL_CATALOG_MAX_BYTES:
                    raise RuntimeError(
                        "The relevant AgentOS tool schemas exceed the web-chat prompt budget; "
                        "narrow the task or use the native AgentOS model provider."
                    )
                context_parts = []
                if system_prompt:
                    context_parts.append("AgentOS runtime context (policy and task data):\n" + system_prompt[:8_000])
                context_parts.append("Available AgentOS tools (names and schemas are authoritative):\n" + catalog_json)
                prompt = build_provider_turn(goal, context="\n\n".join(context_parts))

            try:
                turn = await self.adapter.send_and_wait(
                    tab_id=self.tab_id, prompt=prompt, expected_provider=self.web_chat_provider,
                    expected_conversation_url=self._conversation_url,
                )
            except WebChatContextLimitError:
                # The provider explicitly refused the turn because its own
                # conversation reached a limit. Start one fresh chat, then
                # carry forward the original goal, durable task state and
                # actual tool results. Never replay a tool call here.
                new_url = await self.adapter.start_new_conversation()
                self._conversation_url = new_url
                if new_url and self.run_id:
                    from .session_store import save_web_chat_run_context
                    save_web_chat_run_context(
                        self.run_id, self.web_chat_provider, self.tab_id,
                        conversation_url=new_url,
                    )
                handoff_parts = [
                    "The previous provider conversation reached its context limit. "
                    "This is a continuation of the SAME AgentOS task, not a new task.",
                    "Original user goal:\n" + goal[:30_000],
                    "Continue from the verified task state below. Preserve completed outcomes. "
                    "Do not repeat or assume any previously executed action; request a new AgentOS "
                    "tool action only when the remaining work requires it.",
                ]
                if system_prompt:
                    handoff_parts.append("AgentOS durable task state and runtime policy:\n" + system_prompt[:8_000])
                recorded_results = [
                    str(item.get("content") or "")[:5_000]
                    for item in messages
                    if isinstance(item, dict) and item.get("web_chat_action_result") is True
                ][-5:]
                if not recorded_results and latest_result is not None:
                    recorded_results = [str(latest_result.get("content") or "")[:5_000]]
                if recorded_results:
                    handoff_parts.append(
                        "Recorded AgentOS execution results (authoritative; most recent last):\n"
                        + "\n\n".join(recorded_results)
                    )
                handoff = build_provider_turn(
                    "Continue the original task from the saved progress and recorded results. "
                    "Do not restart completed work.",
                    context="\n\n".join(handoff_parts)[:60_000],
                )
                turn = await self.adapter.send_and_wait(
                    tab_id=self.tab_id, prompt=handoff, expected_provider=self.web_chat_provider,
                    expected_conversation_url=new_url,
                )
            resolved_tab_id = getattr(turn, "tab_id", None)
            if resolved_tab_id is not None:
                self.tab_id = int(resolved_tab_id)
            elif self.tab_id is None:
                raise RuntimeError("The web-chat adapter did not return the automatically resolved Chrome page ID.")
            returned_url = str(getattr(turn, "conversation_url", "") or "")
            if self._conversation_url and returned_url:
                from .adapter import WebChatSessionAdapter
                if WebChatSessionAdapter._conversation_identity(returned_url) != WebChatSessionAdapter._conversation_identity(self._conversation_url):
                    raise RuntimeError("The web-chat provider returned a different conversation; the task was stopped.")
            elif returned_url:
                self._conversation_url = returned_url
            if returned_url and self.run_id:
                from .session_store import save_web_chat_run_context
                save_web_chat_run_context(
                    self.run_id, self.web_chat_provider, self.tab_id,
                    conversation_url=returned_url,
                )
            reply = parse_web_chat_reply(
                turn.assistant_message.text,
                allowed_actions=frozenset(str(tool.get("name") or "") for tool in catalog if tool.get("name")),
            )
            if reply.kind == "final":
                if stream_callback is not None:
                    await stream_callback(reply.text)
                return LLMResponse(content=reply.text, finish_reason="stop")
            action = reply.action
            if action is None:
                raise RuntimeError("The web-chat response did not contain a valid AgentOS action.")
            call_id = action.action_id
            if call_id in self._seen_action_ids:
                raise RuntimeError("The web-chat provider repeated an action ID; duplicate execution was blocked.")
            if self.run_id:
                from .session_store import claim_web_chat_action_id
                claimed = claim_web_chat_action_id(
                    self.run_id, call_id, action.name, action.arguments,
                )
                if not claimed:
                    raise RuntimeError(
                        "The web-chat provider repeated an action ID already reserved by this durable Run; "
                        "duplicate execution was blocked, including across resume."
                    )
            self._seen_action_ids.add(call_id)
            self._last_action_id = call_id
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(
                    call_id=call_id,
                    tool_name=action.name,
                    arguments=action.arguments,
                )],
                finish_reason="tool_calls",
            )

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        response = await self.chat_with_tools(
            messages=messages, tools=[], system_prompt=system_prompt,
            stream_callback=stream_callback,
        )
        return response.content
