"""AgentOS web-chat reasoning loop.

The caller supplies the existing policy-gated tool dispatcher. This coordinator
never invokes a tool directly and never treats model output as authorization.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import jsonschema

from .adapter import WebChatSessionAdapter
from .protocol import (
    WebChatProtocolError,
    build_action_result_message,
    build_provider_turn,
    parse_web_chat_reply,
)


class WebChatRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class WebChatRunResult:
    status: str
    provider: str
    tab_id: int
    steps: int
    final_text: str = ""
    last_action_id: str = ""
    error: str = ""


AuthorizedActionExecutor = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]


class WebChatAgentCoordinator:
    """Drive one task through the same signed-in provider conversation."""

    def __init__(self, adapter: WebChatSessionAdapter, *, max_steps: int = 40) -> None:
        self.adapter = adapter
        self.max_steps = max(1, min(int(max_steps), 200))

    async def run(
        self,
        *,
        tab_id: int,
        provider: str,
        user_goal: str,
        tools: list[dict[str, Any]],
        execute_authorized_action: AuthorizedActionExecutor,
        verified_context: str = "",
    ) -> WebChatRunResult:
        tool_map = {str(item.get("name") or ""): item for item in tools if item.get("name")}
        if not tool_map:
            raise WebChatRunError("No AgentOS tools are available to this task.")
        allowed_names = frozenset(tool_map)
        prompt = build_provider_turn(user_goal, context=verified_context)
        executed_ids: set[str] = set()

        for step in range(1, self.max_steps + 1):
            turn = await self.adapter.send_and_wait(
                tab_id=tab_id, prompt=prompt, expected_provider=provider,
            )
            try:
                reply = parse_web_chat_reply(turn.assistant_message.text, allowed_actions=allowed_names)
            except WebChatProtocolError as exc:
                return WebChatRunResult(
                    status="protocol_error", provider=provider, tab_id=tab_id,
                    steps=step, error=str(exc),
                )
            if reply.kind == "final":
                return WebChatRunResult(
                    status="complete", provider=provider, tab_id=tab_id,
                    steps=step, final_text=reply.text,
                )
            action = reply.action
            if action is None:
                return WebChatRunResult(
                    status="protocol_error", provider=provider, tab_id=tab_id,
                    steps=step, error="The response did not contain a parsed action.",
                )
            if action.action_id in executed_ids:
                return WebChatRunResult(
                    status="duplicate_action", provider=provider, tab_id=tab_id,
                    steps=step, last_action_id=action.action_id,
                    error="The provider repeated an action ID. No duplicate execution was attempted.",
                )
            schema = tool_map[action.name].get("parameters") or {"type": "object"}
            try:
                jsonschema.validate(action.arguments, schema)
            except jsonschema.ValidationError as exc:
                return WebChatRunResult(
                    status="invalid_arguments", provider=provider, tab_id=tab_id,
                    steps=step, last_action_id=action.action_id,
                    error=f"Arguments for {action.name} do not match its AgentOS tool schema: {exc.message}",
                )
            executed_ids.add(action.action_id)
            try:
                result = await execute_authorized_action(action.name, action.arguments, action.action_id)
            except Exception as exc:
                result = {"status": "error", "error": str(exc), "retryable": False}
            if not isinstance(result, dict):
                result = {"status": "error", "error": "The AgentOS dispatcher returned a non-object result."}
            prompt = build_action_result_message(action, result)

        return WebChatRunResult(
            status="step_limit", provider=provider, tab_id=tab_id,
            steps=self.max_steps, error="The task reached the configured web-chat action limit.",
        )
