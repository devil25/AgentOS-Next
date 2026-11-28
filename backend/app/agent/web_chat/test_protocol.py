import pytest

from app.agent.web_chat.protocol import (
    WebChatProtocolError,
    build_action_result_message,
    build_provider_turn,
    parse_web_chat_reply,
    provider_for_url,
)

ALLOWED = {"workspace.list_files", "terminal.execute"}
FENCE = chr(96) * 3


def test_provider_url_matching_is_exact_https_origin():
    assert provider_for_url("https://chatgpt.com/") == "chatgpt"
    assert provider_for_url("https://chat.openai.com/c/123") == "chatgpt"
    assert provider_for_url("https://gemini.google.com/app") == "gemini"
    assert provider_for_url("https://chat.deepseek.com/") == "deepseek"
    assert provider_for_url("https://chat.z.ai/") == "zai"
    assert provider_for_url("https://chat.z.ai.attacker.example/") is None
    assert provider_for_url("http://chat.z.ai/") is None
    assert provider_for_url("https://user:pass@chat.z.ai/") is None
    assert provider_for_url("https://chat.z.ai:8443/") is None


def test_plain_assistant_text_is_a_final_reply():
    reply = parse_web_chat_reply("The task is complete.", allowed_actions=ALLOWED)
    assert reply.kind == "final"
    assert reply.text == "The task is complete."
    assert reply.action is None


def test_final_answer_may_contain_ordinary_markdown_code_fence():
    response = "Example:\n" + FENCE + "python\nprint('hello')\n" + FENCE
    reply = parse_web_chat_reply(response, allowed_actions=ALLOWED)
    assert reply.kind == "final"
    assert "print('hello')" in reply.text


def test_single_registered_action_is_parsed():
    response = """```json
{"protocol":"agentos.v1","type":"action","id":"step-1","action":"terminal.execute","arguments":{"command":"Get-Date"}}
```"""
    reply = parse_web_chat_reply(response, allowed_actions=ALLOWED)
    assert reply.kind == "action"
    assert reply.action is not None
    assert reply.action.name == "terminal.execute"
    assert reply.action.arguments == {"command": "Get-Date"}

@pytest.mark.parametrize("response", [
    'JSON{"protocol":"agentos.v1","type":"action","id":"step-json","action":"terminal.execute","arguments":{"command":"Get-Date"}}',
    '{"protocol":"agentos.v1","type":"action","id":"step-bare","action":"terminal.execute","arguments":{"command":"Get-Date"}}',
])
def test_plain_json_action_with_optional_json_label_is_parsed(response):
    reply = parse_web_chat_reply(response, allowed_actions=ALLOWED)
    assert reply.kind == "action"
    assert reply.action is not None
    assert reply.action.name == "terminal.execute"
    assert reply.action.arguments == {"command": "Get-Date"}


@pytest.mark.parametrize("response", [
    'JSON{"protocol":"agentos.v1","type":"action","id":"x","action":"terminal.execute","arguments":{}} trailing text',
    'JSON{"protocol":"agentos.v1","type":"action","id":"x","action":"terminal.execute","arguments":{}} JSON{"protocol":"agentos.v1"}',
    'Here is the action: JSON{"protocol":"agentos.v1","type":"action","id":"x","action":"terminal.execute","arguments":{}}',
])
def test_plain_json_action_rejects_trailing_or_surrounding_prose(response):
    with pytest.raises(WebChatProtocolError):
        parse_web_chat_reply(response, allowed_actions=ALLOWED)


@pytest.mark.parametrize("response", [
    "",
    """```json
{broken}
```""",
    """```json
{"protocol":"agentos.v1","type":"action","id":"x","action":"not-registered","arguments":{}}
```""",
    """```json
{"protocol":"agentos.v1","type":"action","id":"x","action":"terminal.execute","arguments":{},"extra":true}
```""",
    """Explanation first
```json
{"protocol":"agentos.v1","type":"action","id":"x","action":"terminal.execute","arguments":{}}
```""",
    """```json
{"protocol":"agentos.v1","type":"action","id":"x","action":"terminal.execute","arguments":{}}
```
```json
{}
```""",
    'The action is {"protocol":"agentos.v1"} but malformed.',
])
def test_ambiguous_or_invalid_actions_are_rejected(response):
    with pytest.raises(WebChatProtocolError):
        parse_web_chat_reply(response, allowed_actions=ALLOWED)


def test_result_message_contains_authoritative_execution_result():
    response = """```json
{"protocol":"agentos.v1","type":"action","id":"step-2","action":"workspace.list_files","arguments":{}}
```"""
    action = parse_web_chat_reply(response, allowed_actions=ALLOWED).action
    message = build_action_result_message(action, {"status": "ok", "files": ["a.txt"]})
    assert '"type":"tool_result"' in message
    assert '"status":"ok"' in message
    assert "a.txt" in message


def test_provider_turn_requires_goal_and_bounds_context():
    with pytest.raises(ValueError):
        build_provider_turn(" ")
    prompt = build_provider_turn("Create a report", context="verified result")
    assert "Create a report" in prompt
    assert "agentos.v1" in prompt
    assert "verified result" in prompt
