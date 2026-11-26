from types import SimpleNamespace

import pytest

from app.agent.web_chat.coordinator import WebChatAgentCoordinator
from app.agent.web_chat.adapter import AssistantMessage

FENCE = chr(96) * 3


class FakeAdapter:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def send_and_wait(self, *, tab_id, prompt, expected_provider):
        self.prompts.append((tab_id, prompt, expected_provider))
        return SimpleNamespace(
            assistant_message=AssistantMessage(
                message_id=f"message-{len(self.prompts)}",
                text=self.replies.pop(0),
                complete=True,
            )
        )


def action(action_id="step-1", command="Get-Date"):
    payload = (
        '{"protocol":"agentos.v1","type":"action","id":"' + action_id
        + '","action":"terminal.execute","arguments":{"command":"' + command + '"}}'
    )
    return FENCE + "json\n" + payload + "\n" + FENCE


@pytest.mark.anyio
async def test_coordinator_executes_real_result_then_returns_final_from_same_provider():
    adapter = FakeAdapter([action(), "The command completed successfully."])
    coordinator = WebChatAgentCoordinator(adapter, max_steps=5)
    executed = []

    async def dispatch(name, arguments, action_id):
        executed.append((name, arguments, action_id))
        return {"status": "ok", "stdout": "Thursday"}

    result = await coordinator.run(
        tab_id=5, provider="chatgpt", user_goal="Tell me the date",
        tools=[{
            "name": "terminal.execute",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        }],
        execute_authorized_action=dispatch,
    )
    assert result.status == "complete"
    assert result.final_text == "The command completed successfully."
    assert result.steps == 2
    assert executed == [("terminal.execute", {"command": "Get-Date"}, "step-1")]
    assert "tool_result" in adapter.prompts[1][1]
    assert "Thursday" in adapter.prompts[1][1]
    assert all(item[2] == "chatgpt" for item in adapter.prompts)


@pytest.mark.anyio
async def test_coordinator_rejects_invalid_arguments_before_dispatch():
    adapter = FakeAdapter([action(command="")])
    coordinator = WebChatAgentCoordinator(adapter)
    executed = []

    async def dispatch(*args):
        executed.append(args)
        return {"status": "ok"}

    result = await coordinator.run(
        tab_id=5, provider="chatgpt", user_goal="Do it",
        tools=[{
            "name": "terminal.execute",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "minLength": 1}},
                "required": ["command"],
            },
        }],
        execute_authorized_action=dispatch,
    )
    assert result.status == "invalid_arguments"
    assert executed == []


@pytest.mark.anyio
async def test_coordinator_never_dispatches_unregistered_actions():
    adapter = FakeAdapter([action().replace("terminal.execute", "filesystem.delete")])
    coordinator = WebChatAgentCoordinator(adapter)
    executed = []

    async def dispatch(*args):
        executed.append(args)
        return {"status": "ok"}

    result = await coordinator.run(
        tab_id=5, provider="chatgpt", user_goal="Do it",
        tools=[{"name": "terminal.execute", "parameters": {"type": "object"}}],
        execute_authorized_action=dispatch,
    )
    assert result.status == "protocol_error"
    assert executed == []
