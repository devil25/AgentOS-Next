from __future__ import annotations

import pytest

from app.agent.database import Database
from app.agent.durable.journal import Journal
from app.agent.web_chat.session_store import (
    claim_web_chat_action_id,
    load_web_chat_run_context,
    restore_web_chat_reasoning_client,
    save_web_chat_run_context,
)


def _database(tmp_path):
    db = Database(tmp_path / "agent.db")
    db.init_db()
    db.create_conversation("conversation-1", "Web chat task")
    Journal(db).create_run(
        run_id="run-1",
        conversation_id="conversation-1",
        execution_source="desktop",
        owner_instance_id="owner-1",
        turn_id="turn-1",
    )
    return db


def test_web_chat_run_context_persists_only_provider_and_page_id(tmp_path):
    db = _database(tmp_path)
    save_web_chat_run_context("run-1", "chatgpt", 17, conversation_url="https://chatgpt.com/c/abc?token=do-not-store#response", db=db)

    assert load_web_chat_run_context("run-1", db=db) == {
        "provider": "chatgpt",
        "page_id": 17,
        "conversation_url": "https://chatgpt.com/c/abc",
    }
    restored = restore_web_chat_reasoning_client("run-1", db=db)
    assert restored.web_chat_provider == "chatgpt"
    assert restored.tab_id == 17
    assert restored._conversation_url == "https://chatgpt.com/c/abc"


@pytest.mark.parametrize(
    ("provider", "page_id"),
    [("unknown", 1), ("gemini", -1), ("zai", True)],
)
def test_web_chat_run_context_rejects_invalid_targets(tmp_path, provider, page_id):
    db = _database(tmp_path)
    with pytest.raises(ValueError):
        save_web_chat_run_context("run-1", provider, page_id, db=db)


def test_web_chat_run_context_does_not_restore_for_unbound_run(tmp_path):
    db = _database(tmp_path)
    assert load_web_chat_run_context("run-1", db=db) is None
    assert restore_web_chat_reasoning_client("run-1", db=db) is None



def test_web_chat_run_context_rejects_cross_provider_conversation_url(tmp_path):
    db = _database(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        save_web_chat_run_context(
            "run-1", "chatgpt", 17,
            conversation_url="https://chat.z.ai/c/other",
            db=db,
        )

def test_web_chat_action_ids_are_reserved_once_per_durable_run(tmp_path):
    db = _database(tmp_path)
    assert claim_web_chat_action_id(
        "run-1", "step-1", "terminal.execute", {"command": "Get-Date"}, db=db,
    ) is True
    assert claim_web_chat_action_id(
        "run-1", "step-1", "terminal.execute", {"command": "Get-Date"}, db=db,
    ) is False
    # An ID cannot be reused for a different action payload either.
    assert claim_web_chat_action_id(
        "run-1", "step-1", "fs.write", {"path": "x"}, db=db,
    ) is False
    # The same provider-generated ID is independent in another Run.
    db.create_conversation("conversation-2", "Second web chat task")
    Journal(db).create_run(
        run_id="run-2",
        conversation_id="conversation-2",
        execution_source="desktop",
        owner_instance_id="owner-1",
        turn_id="turn-2",
        workspace_dir=str(tmp_path / "second-workspace"),
    )
    assert claim_web_chat_action_id(
        "run-2", "step-1", "terminal.execute", {"command": "Get-Date"}, db=db,
    ) is True


def test_web_chat_action_id_claim_rejects_unknown_run(tmp_path):
    db = _database(tmp_path)
    with pytest.raises(ValueError, match="unknown Run"):
        claim_web_chat_action_id(
            "missing-run", "step-1", "terminal.execute", {"command": "Get-Date"}, db=db,
        )
