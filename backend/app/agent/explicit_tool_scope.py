"""Parse explicit single-tool restrictions from the current user request.

This is an execution boundary, not a prompt hint: when a user names one tool
and says to use it only, all other model tool calls are withheld from dispatch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_TOOL_TOKEN = r"[A-Za-z][A-Za-z0-9_.:-]*"
_PATTERNS = (
    re.compile(rf"\b(?:use|using|call|invoke|execute)\s+(?:(?:the|only)\s+)?(?:tool\s+)?[`'\"“”]?({_TOOL_TOKEN})[`'\"“”]?\s+only\b", re.IGNORECASE),
    re.compile(rf"\bonly\s+(?:use|call|invoke|execute)\s+(?:(?:the)\s+)?(?:tool\s+)?[`'\"“”]?({_TOOL_TOKEN})[`'\"“”]?", re.IGNORECASE),
)
_ARABIC_PATTERN = re.compile(rf"(?:استخدم|استعمل|نفّذ|نفذ)\s+(?:الأداة\s+)?[`'\"“”]?({_TOOL_TOKEN})[`'\"“”]?\s+فقط")


@dataclass(frozen=True)
class ExplicitToolScope:
    allowed_tool: str

    def allows(self, tool_name: str) -> bool:
        name = str(tool_name or "").strip().lower()
        allowed = self.allowed_tool.lower()
        return name == allowed or name.endswith("__" + allowed) or name.endswith("_" + allowed)


def explicit_tool_scope(user_request: str) -> ExplicitToolScope | None:
    """Return a one-tool scope only when the user explicitly says that tool only."""
    text = str(user_request or "")
    for pattern in (*_PATTERNS, _ARABIC_PATTERN):
        match = pattern.search(text)
        if match:
            return ExplicitToolScope(match.group(1))
    return None
