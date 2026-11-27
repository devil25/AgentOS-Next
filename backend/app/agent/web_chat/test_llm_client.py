from types import SimpleNamespace

import pytest

from app.agent.web_chat.llm_client import WebChatLLMClient
from app.agent.web_chat.adapter import WebChatContextLimitError
from app.agent.harness.tool_runner import ToolBatchRunner
from app.agent.harness.turn_transcript import TurnTranscript
from app.agent.harness.tool_protocol import ToolCallResult

FENCE = chr(96) * 3


class FakeAdapter:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.expected_urls = []
        self.new_chat_calls = 0

    async def send_and_wait(self, *, tab_id, prompt, expected_provider, expected_conversation_url=""):
        self.prompts.append((tab_id, prompt, expected_provider))
        self.expected_urls.append(expected_conversation_url)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        default_url = "https://chatgpt.com/c/task-123" if expected_provider == "chatgpt" else "https://chat.z.ai/c/task-123"
        return SimpleNamespace(
            assistant_message=SimpleNamespace(text=reply),
            conversation_url=expected_conversation_url or default_url,
            tab_id=tab_id,
        )

    async def start_new_conversation(self):
        self.new_chat_calls += 1
        return "https://chatgpt.com/c/fresh-task"


def action():
    payload = (
        '{"protocol":"agentos.v1","type":"action","id":"run-step-1",'
        '"action":"terminal.execute","arguments":{"command":"Get-Date"}}'
    )
    return FENCE + "json\n" + payload + "\n" + FENCE


@pytest.mark.anyio
async def test_web_chat_client_returns_tool_call_for_existing_turn_loop():
    adapter = FakeAdapter([action()])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    tools = [{
        "name": "terminal.execute",
        "description": "Run a PowerShell command",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }]
    response = await client.chat_with_tools(
        messages=[{"role": "user", "content": "What time is it?"}],
        tools=tools,
        system_prompt="Use tools safely.",
    )
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].call_id == "run-step-1"
    assert response.tool_calls[0].tool_name == "terminal.execute"
    assert response.tool_calls[0].arguments == {"command": "Get-Date"}
    assert "terminal.execute" in adapter.prompts[0][1]
    assert adapter.prompts[0][0] == 42


@pytest.mark.anyio
async def test_web_chat_client_rolls_over_once_and_hands_off_goal_and_real_tool_results():
    adapter = FakeAdapter([
        WebChatContextLimitError("The provider explicitly reported its context limit."),
        "The remaining task is complete.",
    ])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    result_message = {
        "role": "user", "content": "AgentOS tool result: created report.pdf",
        "tool_result": True, "web_chat_action_result": True,
    }
    response = await client.chat_with_tools(
        messages=[{"role": "user", "content": "Create a report and verify it"}, result_message],
        tools=[],
        system_prompt="Task progress: report creation verified; verification remains pending.",
    )
    assert response.content == "The remaining task is complete."
    assert adapter.new_chat_calls == 1
    assert len(adapter.prompts) == 2
    handoff = adapter.prompts[1][1]
    assert "Create a report and verify it" in handoff
    assert "report creation verified" in handoff
    assert "created report.pdf" in handoff
    assert "Do not restart completed work" in handoff
    assert adapter.expected_urls == ["", "https://chatgpt.com/c/fresh-task"]


@pytest.mark.anyio
async def test_web_chat_client_does_not_roll_over_twice_for_repeated_context_limit():
    adapter = FakeAdapter([
        WebChatContextLimitError("limit one"),
        WebChatContextLimitError("limit two"),
    ])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    with pytest.raises(WebChatContextLimitError):
        await client.chat_with_tools(
            messages=[{"role": "user", "content": "Continue task"}], tools=[],
        )
    assert adapter.new_chat_calls == 1
    assert len(adapter.prompts) == 2


@pytest.mark.anyio
async def test_web_chat_client_hands_tool_result_back_to_same_web_conversation():
    adapter = FakeAdapter(["The date is Thursday."])
    client = WebChatLLMClient(adapter, provider="zai", tab_id=9)
    result = ToolCallResult.from_output(
        call_id="tool-1",
        name="terminal.execute",
        output='{"status":"ok","stdout":"Thursday"}',
    )
    transcript = TurnTranscript(provider="web_chat")
    transcript.add_tool_result(result)
    messages = [
        {"role": "user", "content": "Tell me the date"},
        transcript.messages[-1],
    ]
    assert messages[-1]["web_chat_action_result"] is True
    response = await client.chat_with_tools(messages=messages, tools=[])
    assert response.content == "The date is Thursday."
    assert response.finish_reason == "stop"
    assert "Thursday" in adapter.prompts[0][1]
    assert adapter.prompts[0][2] == "zai"


@pytest.mark.anyio
async def test_web_chat_client_streams_only_final_user_facing_answer():
    adapter = FakeAdapter(["Finished."])
    client = WebChatLLMClient(adapter, provider="gemini", tab_id=3)
    streamed = []

    async def stream(text):
        streamed.append(text)

    response = await client.chat_with_tools(
        messages=[{"role": "user", "content": "Say done"}],
        tools=[],
        stream_callback=stream,
    )
    assert response.content == "Finished."
    assert streamed == ["Finished."]



@pytest.mark.anyio
async def test_web_chat_client_blocks_repeated_action_ids():
    adapter = FakeAdapter([action(), action()])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    tools = [{
        "name": "terminal.execute",
        "description": "Run a PowerShell command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
    }]
    first = await client.chat_with_tools(
        messages=[{"role": "user", "content": "Check the date"}], tools=tools,
    )
    assert first.finish_reason == "tool_calls"
    with pytest.raises(RuntimeError, match="repeated an action ID"):
        await client.chat_with_tools(
            messages=[
                {"role": "user", "content": "Check the date"},
                {"role": "tool", "tool_result": True, "content": "The date is today"},
            ],
            tools=tools,
        )


@pytest.mark.anyio
async def test_web_chat_client_pins_follow_up_turn_to_original_conversation():
    adapter = FakeAdapter([action(), "All done."])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    tools = [{
        "name": "terminal.execute",
        "description": "Run a PowerShell command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
    }]
    await client.chat_with_tools(
        messages=[{"role": "user", "content": "Check the date"}], tools=tools,
    )
    await client.chat_with_tools(
        messages=[
            {"role": "user", "content": "Check the date"},
            {"role": "tool", "tool_result": True, "content": "The date is today"},
        ],
        tools=[],
    )
    assert adapter.expected_urls == ["", "https://chatgpt.com/c/task-123"]



def test_tool_batch_runner_keeps_review_client_separate_from_web_chat_reasoner():
    web_chat_reasoner = object()
    configured_review_client = object()
    lifecycle = SimpleNamespace(
        conversation_id="web-chat-review-test",
        publisher=SimpleNamespace(publish_nowait=lambda event: None),
    )
    runner = ToolBatchRunner(
        registry=object(),
        executor=object(),
        execution_gate=object(),
        memory=object(),
        client=web_chat_reasoner,
        budget=object(),
        transcript=object(),
        lifecycle=lifecycle,
        observer=SimpleNamespace(conversation_id="web-chat-review-test"),
        steering=None,
        user_request=lambda: "",
        progress=lambda: "",
        max_parallel=1,
        max_identical_calls=2,
        stuck_rounds_limit=3,
        guard_blocks_limit=10,
        review_client=configured_review_client,
    )
    assert runner.llm_client is web_chat_reasoner
    assert runner.review_client is configured_review_client



def test_web_chat_client_binds_the_active_tool_runner_to_chrome_port():
    class BrowserPort:
        def bind_executor(self, executor):
            self.executor = executor

    class Adapter:
        def __init__(self):
            self.browser = BrowserPort()

    adapter = Adapter()
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    executor = object()
    client.bind_browser_tool_executor(executor)
    assert adapter.browser.executor is executor


@pytest.mark.anyio
async def test_web_chat_client_ignores_internal_transport_tool_results_as_model_actions():
    adapter = FakeAdapter(["The task is complete."])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    await client.chat_with_tools(
        messages=[
            {"role": "user", "content": "Original user goal"},
            {"role": "tool", "content": "Internal browser snapshot result"},
        ],
        tools=[],
    )
    assert "Original user goal" in adapter.prompts[0][1]
    assert "Internal browser snapshot result" not in adapter.prompts[0][1]



@pytest.mark.anyio
async def test_transport_tool_dispatch_uses_the_runner_owned_executor(monkeypatch):
    lifecycle = SimpleNamespace(
        conversation_id="transport-dispatch-test",
        publisher=SimpleNamespace(publish_nowait=lambda event: None),
        review_requested=False,
        pause_requested=False,
    )
    runner = ToolBatchRunner(
        registry=object(), executor=object(), execution_gate=object(),
        memory=object(), client=object(), budget=object(), transcript=object(),
        lifecycle=lifecycle, observer=SimpleNamespace(conversation_id="transport-dispatch-test"), steering=None,
        user_request=lambda: "Original task", progress=lambda: "",
        max_parallel=1, max_identical_calls=2, stuck_rounds_limit=3, guard_blocks_limit=10,
    )
    prepared = SimpleNamespace(
        rejection=None,
        policy_decision=SimpleNamespace(allowed=True),
        tool_name="mcp__chrome-devtools__inspect_web_chat",
        arguments={"pageId": 4},
        call_id="transport-call",
    )
    invoked = []

    monkeypatch.setattr(runner, "_prepare", lambda call, steps: prepared)
    monkeypatch.setattr(runner, "_start", lambda item: None)

    async def invoke(item):
        invoked.append((item.tool_name, item.arguments))
        return ToolCallResult.from_output(
            call_id=item.call_id, name=item.tool_name,
            output='{"url":"https://chatgpt.com/"}',
        )

    async def process(item, result, guarded=None, internal_transport=False):
        assert internal_transport is True
        return result

    monkeypatch.setattr(runner, "_invoke_prepared_tool", invoke)
    monkeypatch.setattr(runner, "_process_tool_result", process)
    result = await runner.execute_transport_tool(
        "mcp__chrome-devtools__inspect_web_chat", {"pageId": 4},
    )
    assert invoked == [("mcp__chrome-devtools__inspect_web_chat", {"pageId": 4})]
    assert result["isError"] is False
    assert result["content"][0]["text"] == '{"url":"https://chatgpt.com/"}'



def test_only_real_provider_action_results_are_tagged_for_handoff():
    message = WebChatLLMClient.build_provider_tool_result_message(
        SimpleNamespace(name="terminal.execute", output="verified output"),
    )
    assert message["tool_result"] is True
    assert message["web_chat_action_result"] is True



def test_chat_request_accepts_automatic_provider_discovery_and_validates_optional_page_id():
    from pydantic import ValidationError
    from app.schemas import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(message="Run a task", web_chat_page_id=12)
    with pytest.raises(ValidationError):
        ChatRequest(message="Run a task", web_chat_provider="chatgpt", web_chat_page_id=-1)

    automatic = ChatRequest(message="Run a task", web_chat_provider="chatgpt")
    assert automatic.web_chat_provider == "chatgpt"
    assert automatic.web_chat_page_id is None

    request = ChatRequest(message="Run a task", web_chat_provider="zai", web_chat_page_id=12)
    assert request.web_chat_provider == "zai"
    assert request.web_chat_page_id == 12

def test_web_chat_client_persists_target_before_first_model_request(monkeypatch):
    from app.agent.web_chat import session_store

    saved = []
    monkeypatch.setattr(
        session_store, "save_web_chat_run_context",
        lambda *args, **kwargs: saved.append((args, kwargs)),
    )
    client = WebChatLLMClient(
        FakeAdapter([]), provider="gemini", tab_id=31,
        run_id="durable-run-31",
    )
    client.bind_durable_run(SimpleNamespace(run_id="durable-run-31"))
    assert saved == [
        (("durable-run-31", "gemini", 31), {"conversation_url": ""}),
    ]


def test_web_chat_client_rejects_mismatched_durable_run():
    client = WebChatLLMClient(
        FakeAdapter([]), provider="chatgpt", tab_id=4,
        run_id="run-expected",
    )
    with pytest.raises(RuntimeError, match="does not match"):
        client.bind_durable_run(SimpleNamespace(run_id="different-run"))


def test_web_chat_catalog_is_bounded_and_prioritizes_task_relevant_tools():
    from app.agent.web_chat.llm_client import (
        WEB_CHAT_TOOL_CATALOG_MAX_BYTES,
        _select_web_chat_tool_catalog,
    )

    tools = [
        {
            "name": f"unrelated.tool_{index}",
            "description": "Large unrelated description " * 200,
            "parameters": {"type": "object", "properties": {"unused": {"type": "string", "description": "x" * 200}}},
        }
        for index in range(80)
    ]
    tools.append({
        "name": "terminal.execute",
        "description": "Run a PowerShell command in the terminal",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Command to execute"}},
            "required": ["command"],
        },
    })

    catalog = _select_web_chat_tool_catalog(tools, "Run a PowerShell command")
    encoded = __import__("json").dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded.encode("utf-8")) <= WEB_CHAT_TOOL_CATALOG_MAX_BYTES
    assert catalog[0]["name"] == "terminal.execute"


@pytest.mark.anyio
async def test_web_chat_client_handles_large_global_catalog_by_sending_relevant_subset():
    adapter = FakeAdapter([action()])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    tools = [
        {
            "name": f"unrelated.tool_{index}",
            "description": "Unrelated long description " * 100,
            "parameters": {"type": "object", "properties": {"unused": {"type": "string"}}},
        }
        for index in range(100)
    ]
    tools.append({
        "name": "terminal.execute",
        "description": "Run a PowerShell command",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    })
    response = await client.chat_with_tools(
        messages=[{"role": "user", "content": "Run a PowerShell command"}],
        tools=tools,
    )
    assert response.finish_reason == "tool_calls"
    assert "terminal.execute" in adapter.prompts[0][1]
    assert "unrelated.tool_99" not in adapter.prompts[0][1]



@pytest.mark.anyio
async def test_web_chat_client_keeps_original_goal_when_internal_browser_guidance_follows_tool_result():
    adapter = FakeAdapter(["The captured answer is ready."])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    user = {"role": "user", "content": "Read the latest ChatGPT response"}
    result = {
        "role": "user",
        "content": "AgentOS tool result for embedded_browser_conversation: pageText=Captured answer",
        "tool_result": True,
        "web_chat_action_result": True,
    }
    internal_hint = {
        "role": "user",
        "content": "Browser workflow guidance: The latest exchange is already captured. Answer now.",
        "agentos_internal_context": True,
    }

    response = await client.chat_with_tools(
        messages=[user, result, internal_hint],
        tools=[],
    )

    assert response.content == "The captured answer is ready."
    assert "Original AgentOS request:" in adapter.prompts[0][1]
    assert "Read the latest ChatGPT response" in adapter.prompts[0][1]
    assert "Browser workflow guidance:" not in adapter.prompts[0][1]


@pytest.mark.anyio
async def test_web_chat_client_stops_consecutive_identical_conversation_reads_with_new_action_ids():
    def read_action(action_id):
        payload = {
            "protocol": "agentos.v1",
            "type": "action",
            "id": action_id,
            "action": "embedded_browser_conversation",
            "arguments": {},
        }
        import json
        return FENCE + "json\n" + json.dumps(payload) + "\n" + FENCE

    adapter = FakeAdapter([
        read_action("read-1"),
        read_action("read-2"),
        read_action("read-3"),
    ])
    client = WebChatLLMClient(adapter, provider="chatgpt", tab_id=42)
    tools = [{
        "name": "embedded_browser_conversation",
        "description": "Read the active embedded AI conversation",
        "parameters": {"type": "object", "properties": {}},
    }]
    user = {"role": "user", "content": "Read the response in the browser"}
    first = await client.chat_with_tools(messages=[user], tools=tools)
    assert first.finish_reason == "tool_calls"
    result_one = {
        "role": "user",
        "content": "AgentOS tool result for embedded_browser_conversation: captureSource=visible_page_text_fallback; pageText=answer",
        "tool_result": True,
        "web_chat_action_result": True,
    }
    second = await client.chat_with_tools(messages=[user, result_one], tools=tools)
    assert second.finish_reason == "tool_calls"
    assert "Do not repeat the read or call embedded_browser_conversation" in adapter.prompts[1][1]
    assert "pageText=answer" in adapter.prompts[1][1]
    assert "Original AgentOS request:" in adapter.prompts[1][1]
    assert "Read the response in the browser" in adapter.prompts[1][1]
    assert "You are the reasoning engine for AgentOS-Next" not in adapter.prompts[1][1]
    result_two = {
        "role": "user",
        "content": "AgentOS tool result: same pageText=answer",
        "tool_result": True,
        "web_chat_action_result": True,
    }
    with pytest.raises(RuntimeError, match="repeated embedded_browser_conversation"):
        await client.chat_with_tools(messages=[user, result_one, result_two], tools=tools)
    assert len(adapter.prompts) == 3
