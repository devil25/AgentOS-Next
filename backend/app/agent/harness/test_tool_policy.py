from app.agent.harness.tool_policy import ToolPolicy


def test_browser_observation_and_planning_calls_are_repeat_safe():
    assert ToolPolicy._is_repeat_safe("embedded_browser_conversation", {})
    assert ToolPolicy._is_repeat_safe("embedded_browser_snapshot", {})
    assert ToolPolicy._is_repeat_safe("embedded_browser_read", {})
    assert ToolPolicy._is_repeat_safe("list_pages", {})
    assert ToolPolicy._is_repeat_safe("mcp__chrome_devtools__list_pages", {})
    assert ToolPolicy._is_repeat_safe("task_plan", {})


def test_repeated_state_changing_actions_remain_guarded():
    assert not ToolPolicy._is_repeat_safe("exec", {})
    assert not ToolPolicy._is_repeat_safe("embedded_browser_press_key", {"key": "Enter"})
    assert not ToolPolicy._is_repeat_safe("embedded_browser_click", {"index": 3})
    assert not ToolPolicy._is_repeat_safe("embedded_browser_type", {"text": "hello"})


def test_enter_retry_is_rearmed_only_after_a_successful_new_composer_edit():
    from app.agent.harness.tool_policy import tool_call_signature

    policy = ToolPolicy(max_identical_calls=1)
    enter_signature = tool_call_signature("embedded_browser_press_key", {"key": "Enter"})
    policy._call_counts[enter_signature] = 1

    # A read or unrelated browser mutation must not make an identical Enter safe.
    policy.note_successful_page_action("embedded_browser_snapshot")
    assert policy._call_counts[enter_signature] == 1
    policy.note_successful_page_action("embedded_browser_click")
    assert policy._call_counts[enter_signature] == 1

    # Only a successfully completed new text entry opens a fresh send context.
    policy.note_successful_page_action("embedded_browser_type")
    assert enter_signature not in policy._call_counts
