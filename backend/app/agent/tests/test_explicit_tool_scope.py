import pytest

from app.agent.explicit_tool_scope import explicit_tool_scope


@pytest.mark.parametrize("request,allowed", [
    ("Use the tool `list_pages` only.", "list_pages"),
    ("Use `list_pages` only; do not call anything else.", "list_pages"),
    ("Only use tool `list_pages`.", "list_pages"),
    ("استخدم أداة `list_pages` فقط.", "list_pages"),
    ("نفّذ `list_pages` فقط.", "list_pages"),
])
def test_explicit_single_tool_scope_is_detected(request, allowed):
    scope = explicit_tool_scope(request)
    assert scope is not None
    assert scope.allowed_tool == allowed


@pytest.mark.parametrize("request", [
    "List the open Chrome pages.",
    "Use list_pages and then inspect the page.",
    "Do not use list_pages.",
    "Use the browser carefully.",
])
def test_no_scope_is_inferred_without_an_explicit_only_restriction(request):
    assert explicit_tool_scope(request) is None


def test_scope_matches_namespaced_mcp_tool_names_without_allowing_other_tools():
    scope = explicit_tool_scope("Use `list_pages` only.")
    assert scope is not None
    assert scope.allows("list_pages")
    assert scope.allows("mcp__chrome_devtools__list_pages")
    assert not scope.allows("inspect_web_chat")
    assert not scope.allows("mcp__chrome_devtools__inspect_web_chat")
