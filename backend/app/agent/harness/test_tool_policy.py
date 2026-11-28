from app.agent.harness.tool_policy import ToolPolicy


def test_browser_observation_and_planning_calls_are_repeat_safe():
    assert ToolPolicy._is_repeat_safe("embedded_browser_conversation", {})
    assert ToolPolicy._is_repeat_safe("embedded_browser_snapshot", {})
    assert ToolPolicy._is_repeat_safe("embedded_browser_read", {})
    assert ToolPolicy._is_repeat_safe("list_pages", {})
    assert ToolPolicy._is_repeat_safe("task_plan", {})


def test_repeated_state_changing_actions_remain_guarded():
    assert not ToolPolicy._is_repeat_safe("exec", {})
    assert not ToolPolicy._is_repeat_safe("embedded_browser_press_key", {"key": "Enter"})
    assert not ToolPolicy._is_repeat_safe("embedded_browser_click", {"index": 3})
    assert not ToolPolicy._is_repeat_safe("embedded_browser_type", {"text": "hello"})
