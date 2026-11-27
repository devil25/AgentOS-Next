import pytest

from app.agent.explicit_tool_scope import ExplicitToolScopeGuard, explicit_tool_scope, filter_tool_calls


@pytest.mark.parametrize("user_text,allowed", [
    ("Use the tool `list_pages` only.", "list_pages"),
    ("Use `list_pages` only; do not call anything else.", "list_pages"),
    ("Only use tool `list_pages`.", "list_pages"),
    ("استخدم أداة `list_pages` فقط.", "list_pages"),
    ("نفّذ `list_pages` فقط.", "list_pages"),
    ("نفّذ اختبار اتصال Chrome باستخدام أداة `list_pages` فقط.", "list_pages"),
    ("نفّذ الاختبار باستخدام أداة `list_pages` فقط.", "list_pages"),
])
def test_explicit_single_tool_scope_is_detected(user_text, allowed):
    scope = explicit_tool_scope(user_text)
    assert scope is not None
    assert scope.allowed_tool == allowed


@pytest.mark.parametrize("user_text", [
    "List the open Chrome pages.",
    "Use list_pages and then inspect the page.",
    "Do not use list_pages.",
    "Use the browser carefully.",
])
def test_no_scope_is_inferred_without_an_explicit_only_restriction(user_text):
    assert explicit_tool_scope(user_text) is None


def test_scope_matches_namespaced_mcp_tool_names_without_allowing_other_tools():
    scope = explicit_tool_scope("Use `list_pages` only.")
    assert scope is not None
    assert scope.allows("list_pages")
    assert scope.allows("mcp__chrome_devtools__list_pages")
    assert not scope.allows("inspect_web_chat")
    assert not scope.allows("mcp__chrome_devtools__inspect_web_chat")


def test_only_allowed_tool_is_retained_and_extra_calls_are_blocked_before_dispatch():
    scope = explicit_tool_scope("Use the tool `list_pages` only.")
    assert scope is not None
    calls = [
        {"name": "list_pages", "arguments": {}},
        {"name": "inspect_web_chat", "arguments": {"pageId": 1}},
    ]
    allowed, blocked = filter_tool_calls(scope, calls)
    assert allowed == [calls[0]]
    assert blocked == ["inspect_web_chat"]


def test_namespaced_allowed_tool_is_retained_but_other_mcp_tools_are_blocked():
    scope = explicit_tool_scope("Use `list_pages` only.")
    assert scope is not None
    calls = [
        {"name": "mcp__chrome_devtools__list_pages", "arguments": {}},
        {"name": "mcp__chrome_devtools__inspect_web_chat", "arguments": {"pageId": 1}},
    ]
    allowed, blocked = filter_tool_calls(scope, calls)
    assert allowed == [calls[0]]
    assert blocked == ["mcp__chrome_devtools__inspect_web_chat"]



def test_scope_is_latched_from_original_request_across_later_tool_result_rounds():
    guard = ExplicitToolScopeGuard()
    original = "نفّذ اختبار اتصال Chrome باستخدام أداة `list_pages` فقط."
    scope = guard.resolve(original)
    assert scope is not None
    assert scope.allows("mcp__chrome-devtools__list_pages")

    # A later user/tool-result context must not erase the original restriction.
    assert guard.resolve("AgentOS tool result: pages listed") is scope
    assert not scope.allows("mcp__chrome-devtools__inspect_web_chat")


def test_scope_guard_does_not_allow_a_later_request_to_broaden_an_unrestricted_turn():
    guard = ExplicitToolScopeGuard()
    assert guard.resolve("List the open Chrome pages.") is None
    assert guard.resolve("Use `list_pages` only.") is None
