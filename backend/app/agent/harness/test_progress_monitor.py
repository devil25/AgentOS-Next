import json

from app.agent.harness.progress_monitor import ProgressMonitor, semantic_result


def test_companion_heartbeat_metadata_is_not_semantic_progress():
    first = {
        "provider": "chatgpt",
        "documentId": "document-1",
        "sequence": 1,
        "observedAt": 1000,
        "sourceObservedAt": 990,
        "stableForMs": 900,
        "messages": [{"message_id": "assistant-1", "role": "assistant", "text": "Finished", "complete": True}],
    }
    second = {
        **first,
        "sequence": 2,
        "observedAt": 6000,
        "sourceObservedAt": 5990,
        "stableForMs": 5900,
    }
    assert semantic_result(first) == semantic_result(second)

    monitor = ProgressMonitor()
    tool = {"metadata": {"observation": True, "mutates_state": False}}
    monitor.begin_round()
    monitor.observe("embedded_browser_conversation", {}, json.dumps(first), "ok", tool)
    assert monitor.finish_round() == ("", False)
    monitor.begin_round()
    monitor.observe("embedded_browser_conversation", {}, json.dumps(second), "ok", tool)
    hint, stalled = monitor.finish_round()
    assert not stalled
    assert not hint
    assert monitor.stalled_rounds == 1


def test_provider_generation_wait_is_not_stopped_by_no_progress_rounds():
    monitor = ProgressMonitor()
    tool = {"metadata": {"observation": True, "mutates_state": False}}
    for sequence in range(1, 21):
        monitor.begin_round()
        payload = {
            "provider": "chatgpt",
            "generating": True,
            "pendingSubmission": True,
            "sequence": sequence,
            "observedAt": sequence * 1000,
            "messages": [],
        }
        monitor.observe("embedded_browser_conversation", {}, json.dumps(payload), "ok", tool)
        hint, stalled = monitor.finish_round()
        assert not hint
        assert not stalled
        assert monitor.stalled_rounds == 0
