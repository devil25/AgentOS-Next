"""Persist the non-secret provider/page binding needed to resume web-chat Runs.

No cookies, passwords, browser storage, or provider tokens are read or stored.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.agent.database import Database, get_db

SUPPORTED_WEB_CHAT_PROVIDERS = frozenset({"chatgpt", "gemini", "deepseek", "zai"})


def save_web_chat_run_context(
    run_id: str, provider: str, page_id: int, *, conversation_url: str = "", db: Database | None = None,
) -> None:
    if provider not in SUPPORTED_WEB_CHAT_PROVIDERS:
        raise ValueError("Unsupported web-chat provider.")
    if isinstance(page_id, bool) or not isinstance(page_id, int) or page_id < 1:
        raise ValueError("A valid Chrome DevTools page ID is required.")
    if not isinstance(conversation_url, str) or len(conversation_url) > 4096:
        raise ValueError("The provider conversation URL is invalid.")
    if conversation_url:
        from app.agent.web_chat.protocol import provider_for_url
        if provider_for_url(conversation_url) != provider:
            raise ValueError("The provider conversation URL does not match the selected provider.")
        parsed = urlsplit(conversation_url)
        # Conversation identity is host + path; never persist query or fragment data.
        conversation_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    database = db or get_db()
    database.init_db()
    with database.transaction() as conn:
        exists = conn.execute("SELECT 1 FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        if exists is None:
            raise ValueError("Cannot bind a web-chat target to an unknown Run.")
        conn.execute(
            """INSERT INTO web_chat_run_context (run_id, provider, page_id, conversation_url, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                 provider = excluded.provider,
                 page_id = excluded.page_id,
                 conversation_url = CASE WHEN excluded.conversation_url <> '' THEN excluded.conversation_url ELSE web_chat_run_context.conversation_url END,
                 created_at = excluded.created_at""",
            (run_id, provider, page_id, str(conversation_url or "")[:4096], datetime.now(timezone.utc).isoformat()),
        )


def claim_web_chat_action_id(
    run_id: str, action_id: str, tool_name: str, arguments: dict[str, Any], *,
    db: Database | None = None,
) -> bool:
    """Atomically reserve an action ID for one durable Run.

    This is an at-most-once fence across Run resume/restart. A claimed ID is
    never silently made reusable after an interrupted or uncertain dispatch.
    Only a digest of arguments is stored; argument contents are not persisted.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("A durable Run ID is required for action idempotency.")
    if not isinstance(action_id, str) or not action_id or len(action_id) > 96:
        raise ValueError("The web-chat action ID is invalid.")
    if not isinstance(tool_name, str) or not tool_name.strip() or len(tool_name) > 256:
        raise ValueError("The web-chat tool name is invalid.")
    if not isinstance(arguments, dict):
        raise ValueError("Web-chat action arguments must be an object.")
    canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    database = db or get_db()
    database.init_db()
    with database.transaction() as conn:
        exists = conn.execute("SELECT 1 FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        if exists is None:
            raise ValueError("Cannot reserve an action ID for an unknown Run.")
        cursor = conn.execute(
            """INSERT OR IGNORE INTO web_chat_action_ids
               (run_id, action_id, tool_name, arguments_sha256, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, action_id, tool_name, digest, datetime.now(timezone.utc).isoformat()),
        )
        return cursor.rowcount == 1


def load_web_chat_run_context(run_id: str, *, db: Database | None = None) -> dict[str, Any] | None:
    database = db or get_db()
    database.init_db()
    row = database.fetchone(
        "SELECT provider, page_id, conversation_url FROM web_chat_run_context WHERE run_id = ?",
        (run_id,),
    )
    if row is None:
        return None
    provider = str(row["provider"] or "")
    page_id = int(row["page_id"])
    if provider not in SUPPORTED_WEB_CHAT_PROVIDERS or page_id < 1:
        return None
    return {"provider": provider, "page_id": page_id, "conversation_url": str(row["conversation_url"] or "")}


def build_web_chat_reasoning_client(
    provider: str, page_id: int, *, run_id: str = "", conversation_url: str = "",
):
    from app.agent.web_chat.adapter import WebChatSessionAdapter
    from app.agent.web_chat.chrome_port import ChromeWebChatBrowserPort
    from app.agent.web_chat.llm_client import WebChatLLMClient

    if provider not in SUPPORTED_WEB_CHAT_PROVIDERS:
        raise ValueError("Unsupported web-chat provider.")
    if isinstance(page_id, bool) or not isinstance(page_id, int) or page_id < 0:
        raise ValueError("A valid Chrome DevTools page ID is required.")
    def persist_conversation_url(url: str) -> None:
        if run_id:
            save_web_chat_run_context(
                run_id, provider, page_id, conversation_url=url,
            )

    return WebChatLLMClient(
        WebChatSessionAdapter(
            ChromeWebChatBrowserPort(),
            on_conversation_url=persist_conversation_url if run_id else None,
        ),
        provider=provider,
        tab_id=page_id,
        run_id=run_id,
        conversation_url=conversation_url,
    )


def restore_web_chat_reasoning_client(run_id: str, *, db: Database | None = None):
    target = load_web_chat_run_context(run_id, db=db)
    if target is None:
        return None
    return build_web_chat_reasoning_client(
        target["provider"], target["page_id"], run_id=run_id,
        conversation_url=target["conversation_url"],
    )
