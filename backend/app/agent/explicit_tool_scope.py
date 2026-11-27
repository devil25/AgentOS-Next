"""Parse and retain explicit single-tool restrictions for one AgentOS turn."""
from __future__ import annotations

import re
from dataclasses import dataclass


_TOOL_TOKEN = r"[A-Za-z][A-Za-z0-9_.:-]*"
# Strip Markdown/quote delimiters before matching. Tool names are protocol
# identifiers, so punctuation around them must not become part of the name.
_DELIMITERS = re.compile(r"[\x60'\"“”‘’]")
_PATTERNS = (
    re.compile(
        rf"\b(?:use|using|call|invoke|execute)\s+"
        rf"(?:(?:the|only)\s+)?(?:tool\s+)?({_TOOL_TOKEN})\s+only\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bonly\s+(?:use|call|invoke|execute)\s+"
        rf"(?:(?:the)\s+)?(?:tool\s+)?({_TOOL_TOKEN})",
        re.IGNORECASE,
    ),
)
_ARABIC_PATTERN = re.compile(
    rf"(?:استخدم|استعمل|نفّذ|نفذ)\s+(?:(?:الأداة|أداة)\s+)?"
    rf"({_TOOL_TOKEN})\s+فقط"
)
_ARABIC_USING_PATTERN = re.compile(
    rf"باستخدام\s+(?:(?:الأداة|أداة)\s+)?({_TOOL_TOKEN})\s+فقط"
)


@dataclass(frozen=True)
class ExplicitToolScope:
    allowed_tool: str

    def allows(self, tool_name: str) -> bool:
        name = str(tool_name or "").strip().lower()
        allowed = self.allowed_tool.lower()
        return name == allowed or name.endswith("__" + allowed) or name.endswith("_" + allowed)


class ExplicitToolScopeGuard:
    """Latch the original user restriction for the lifetime of one turn."""

    def __init__(self) -> None:
        self._resolved = False
        self._scope: ExplicitToolScope | None = None

    def resolve(self, user_request: str) -> ExplicitToolScope | None:
        if not self._resolved:
            self._scope = explicit_tool_scope(user_request)
            self._resolved = True
        return self._scope


def filter_tool_calls(scope: ExplicitToolScope, calls: list[dict]) -> tuple[list[dict], list[str]]:
    allowed: list[dict] = []
    blocked: list[str] = []
    for call in calls:
        name = str(call.get("name") or "")
        if scope.allows(name):
            allowed.append(call)
        else:
            blocked.append(name or "unknown")
    return allowed, blocked


def explicit_tool_scope(user_request: str) -> ExplicitToolScope | None:
    text = _DELIMITERS.sub("", str(user_request or ""))
    for pattern in (*_PATTERNS, _ARABIC_PATTERN, _ARABIC_USING_PATTERN):
        match = pattern.search(text)
        if match:
            return ExplicitToolScope(match.group(1))
    return None
