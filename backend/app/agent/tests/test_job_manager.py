import asyncio

from app.agent.job_manager import JobState, RunState


def test_job_done_event_with_incomplete_flag_sets_paused_status():
    job = JobState(job_id="job-1", conversation_id="conv-1")

    asyncio.run(job.append_event({
        "event": "done",
        "data": {
            "conversation_id": "conv-1",
            "summary": "Paused.",
            "incomplete": True,
            "reason_code": "iteration_limit_reached_after_tool",
        },
    }))

    assert job.status == "paused"



def test_job_state_uses_typed_terminal_transitions():
    job = JobState(job_id="job-1", conversation_id="conv-1")

    job.transition(RunState.CANCELLED)
    job.transition(RunState.DONE)

    assert job.status == RunState.CANCELLED
    assert job.status == "cancelled"


def test_job_append_event_counts_bounded_subscriber_overflow():
    job = JobState(job_id="job-1", conversation_id="conv-1")
    queue = asyncio.Queue(maxsize=1)
    job._subscribers.append(queue)

    asyncio.run(job.append_event({"event": "token", "data": {"content": "one"}}))
    asyncio.run(job.append_event({"event": "token", "data": {"content": "two"}}))

    assert queue.qsize() == 1
    assert job.dropped_subscriber_events == 1

def test_detached_job_passes_web_chat_reasoning_client(monkeypatch):
    from app.agent import runtime
    from app.agent.job_manager import run_job

    captured = {}
    reasoning_client = object()

    async def fake_run_agent_stream(**kwargs):
        captured.update(kwargs)
        yield {"event": "done", "data": {"conversation_id": "conv-1"}}

    monkeypatch.setattr(runtime, "run_agent_stream", fake_run_agent_stream)
    job = JobState(job_id="job-web-chat", conversation_id="conv-1")

    asyncio.run(run_job(
        job,
        "Use the selected web chat",
        settings=object(),
        reasoning_client=reasoning_client,
    ))

    assert captured["reasoning_client"] is reasoning_client
    assert captured["durable_run_id"] == "job-web-chat"
    assert job.status == RunState.DONE
