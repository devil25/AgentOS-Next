"""LLMClient-compatible adapter that uses a signed-in web-chat UI.

Tool calls are returned to AgentOS TurnLoop as normal ToolCallRequests, so the
existing ToolBatchRunner, execution gate, approvals, journaling and verification
remain the only path that can execute tools.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from .adapter import WebChatSessionAdapter
from .protocol import action_instruction, build_provider_turn, parse_web_chat_reply


class WebChatLLMClient:
    def __init__(
        self,
        adapter: WebChatSessionAdapter,
        *,
        provider: str,
        tab_id: int,
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
        self.tab_id = int(tab_id)
        self._last_action_id = ""
        self._conversation_url = str(conversation_url or "")
        self.run_id = str(run_id or "")
        self._seen_action_ids: set[str] = set()
        self._call_lock = asyncio.Lock()

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
            if latest_result is not None:
                result_text = str(latest_result.get("content") or "")
                prompt = (
                    "Continue the same task using this actual AgentOS execution result:\n"
                    + result_text + "\n\n" + action_instruction()
                )
            else:
                latest_user = next(
                    (item for item in reversed(messages)
                     if isinstance(item, dict) and item.get("role") == "user" and not item.get("tool_result")),
                    None,
                )
                goal = str((latest_user or {}).get("content") or "").strip()
                if not goal:
                    raise RuntimeError("The web-chat provider did not receive a user task.")
                catalog = [
                    {
                        "name": str(tool.get("name") or ""),
                        "description": str(tool.get("description") or ""),
                        "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                    }
                    for tool in tools if tool.get("name")
                ]
                catalog_json = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
                if len(catalog_json) > 50_000:
                    raise RuntimeError("The available AgentOS tool catalog is too large for the web-chat prompt.")
                context_parts = []
                if system_prompt:
                    context_parts.append("AgentOS runtime context (policy and task data):\n" + system_prompt[:8_000])
                context_parts.append("Available AgentOS tools (names and schemas are authoritative):\n" + catalog_json)
                prompt = build_provider_turn(goal, context="\n\n".join(context_parts))

            turn = await self.adapter.send_and_wait(
                tab_id=self.tab_id, prompt=prompt, expected_provider=self.web_chat_provider,
                expected_conversation_url=self._conversation_url,
            )
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
                allowed_actions=frozenset(str(tool.get("name") or "") for tool in tools if tool.get("name")),
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
