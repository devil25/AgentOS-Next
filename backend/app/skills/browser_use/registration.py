from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .tools import (
    browser_fetch, embedded_browser_click, embedded_browser_navigate, embedded_browser_press_key,
    embedded_browser_conversation, embedded_browser_read, embedded_browser_snapshot, embedded_browser_type,
)


def _configured_allowed_domains(settings: Any) -> list[str]:
    browser = getattr(settings, "browser", None)
    if isinstance(browser, dict):
        raw = browser.get("allowed_domains")
    else:
        raw = getattr(browser, "allowed_domains", None)
    return [str(item).strip() for item in (raw or []) if str(item).strip()]


def register_browser_tools(registry, settings) -> None:
    # Interactive browsing is provided by the built-in browser server that the
    # Monaw extension connects to (mcp_bridge/builtin.py, browser_use/extension);
    # this skill's only native tool is the HTTP fetcher.
    policy = SimpleNamespace(config={"allowed_domains": _configured_allowed_domains(settings)})

    registry.extend(
        [
            {
                "name": "browser_fetch",
                "description": (
                    "Read-only HTTP fetch of a known URL, extracted with Scrapling (no browser, no JavaScript). "
                    "Use it to read or summarize a page before opening a tab. If it returns "
                    "status \"needs_browser_render\", open the page with mcp__chrome-devtools__new_page "
                    "or navigate_page and read it with take_snapshot."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "mode": {
                            "type": "string",
                            "enum": ["auto", "http"],
                            "default": "auto",
                            "description": (
                                "auto: flag pages that look JavaScript-dependent or blocked; http: return the raw HTTP "
                                "result as is."
                            ),
                        },
                        "selector": {"type": "string", "default": ""},
                        "output": {"type": "string", "enum": ["text", "markdown", "html", "links", "metadata"], "default": "text"},
                        "max_chars": {"type": "integer", "default": 20000},
                        "timeout_ms": {"type": "integer", "default": 30000},
                    },
                    "required": ["url"],
                },
                "callable": lambda url, mode="auto", selector="", output="text", max_chars=20000, timeout_ms=30000, **_unused: browser_fetch(
                    policy,
                    url=url,
                    mode=mode,
                    selector=selector,
                    output=output,
                    max_chars=max_chars,
                    timeout_ms=timeout_ms,
                ),
                "domain": "browser",
                "execution_mode": "async",
                # Fetches are capped at 120 s; leave room for parsing.
                "timeout_seconds": 130.0,
                "metadata": {
                    "observation": True,
                    "mutates_state": False,
                    "risk_level": "low",
                    "parallel_safe": True,
                    "resource_locks": [],
                    "repeat_safe": True,
                },
            },
            {
                "name": "embedded_browser_read",
                "description": (
                    "Read the currently open website inside the AgentOS-Next embedded browser. "
                    "Returns the visible page title, URL, headings, and text. This is read-only; "
                    "it does not access cookies, passwords, local storage, or session tokens. "
                    "Use this when the user asks you to inspect or read the page open in AgentOS Browser."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_chars": {"type": "integer", "default": 20000, "minimum": 500, "maximum": 20000},
                    },
                    "required": [],
                },
                "callable": lambda max_chars=20000, **_unused: embedded_browser_read(max_chars=max_chars),
                "domain": "browser",
                "execution_mode": "async",
                "timeout_seconds": 10.0,
                "metadata": {
                    "observation": True,
                    "mutates_state": False,
                    "risk_level": "low",
                    "parallel_safe": True,
                    "resource_locks": ["embedded_browser"],
                    "repeat_safe": True,
                },
            },
            {
                "name": "embedded_browser_conversation",
                "description": "Read structured messages, composer readiness, login state, and generation state from the active embedded ChatGPT, Gemini, DeepSeek, or Z.ai conversation. Read-only observation.",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "callable": lambda **_unused: embedded_browser_conversation(),
                "domain": "browser", "execution_mode": "async", "timeout_seconds": 15.0,
                "metadata": {"observation": True, "mutates_state": False, "risk_level": "low", "parallel_safe": False, "resource_locks": ["embedded_browser"], "repeat_safe": True},
            },
            {
                "name": "embedded_browser_snapshot",
                "description": "List visible interactive elements in the active AgentOS embedded page. Use the returned indexes only until the page changes; take a fresh snapshot before acting.",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "callable": lambda **_unused: embedded_browser_snapshot(),
                "domain": "browser", "execution_mode": "async", "timeout_seconds": 15.0,
                "metadata": {"observation": True, "mutates_state": False, "risk_level": "low", "parallel_safe": False, "resource_locks": ["embedded_browser"], "repeat_safe": True},
            },
            {
                "name": "embedded_browser_click",
                "description": "Click a visible element by index from the latest embedded_browser_snapshot. This changes the website; use only when the user requested the action or it is necessary for the task.",
                "parameters": {"type": "object", "properties": {"index": {"type": "integer", "minimum": 0, "maximum": 199}}, "required": ["index"]},
                "callable": lambda index, **_unused: embedded_browser_click(index=index),
                "domain": "browser", "execution_mode": "async", "timeout_seconds": 15.0,
                "metadata": {"observation": False, "mutates_state": True, "risk_level": "medium", "parallel_safe": False, "resource_locks": ["embedded_browser"], "repeat_safe": False},
            },
            {
                "name": "embedded_browser_type",
                "description": "Replace text in a visible non-password text field by index from the latest embedded_browser_snapshot. Does not submit the form.",
                "parameters": {"type": "object", "properties": {"index": {"type": "integer", "minimum": 0, "maximum": 199}, "text": {"type": "string", "maxLength": 4000}}, "required": ["index", "text"]},
                "callable": lambda index, text, **_unused: embedded_browser_type(index=index, text=text),
                "domain": "browser", "execution_mode": "async", "timeout_seconds": 15.0,
                "metadata": {"observation": False, "mutates_state": True, "risk_level": "medium", "parallel_safe": False, "resource_locks": ["embedded_browser"], "repeat_safe": False},
            },
            {
                "name": "embedded_browser_press_key",
                "description": "Send one allow-listed key (Enter, Escape, Tab, ArrowUp, ArrowDown, Backspace) to the active embedded page. Enter may submit a form or send a chat message.",
                "parameters": {"type": "object", "properties": {"key": {"type": "string", "enum": ["Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "Backspace"]}}, "required": ["key"]},
                "callable": lambda key, **_unused: embedded_browser_press_key(key=key),
                "domain": "browser", "execution_mode": "async", "timeout_seconds": 15.0,
                "metadata": {"observation": False, "mutates_state": True, "risk_level": "medium", "parallel_safe": False, "resource_locks": ["embedded_browser"], "repeat_safe": False},
            },
            {
                "name": "embedded_browser_navigate",
                "description": "Navigate the active AgentOS embedded browser to a supplied HTTPS URL. Navigation is limited to HTTPS and cannot include embedded credentials.",
                "parameters": {"type": "object", "properties": {"url": {"type": "string", "maxLength": 4096}}, "required": ["url"]},
                "callable": lambda url, **_unused: embedded_browser_navigate(url=url),
                "domain": "browser", "execution_mode": "async", "timeout_seconds": 20.0,
                "metadata": {"observation": False, "mutates_state": True, "risk_level": "medium", "parallel_safe": False, "resource_locks": ["embedded_browser"], "repeat_safe": False},
            }
        ]
    )
