"""def-stub-generator — fill missing-definition gaps with staged stubs (v1.4).

When statement-verifier returns ``needs_definition: ["algebra.x"]``,
this agent creates a staged definition stub for ``algebra.x`` with
either a first-pass draft body (``drafted``) or a TODO placeholder
(``placeholder_only``). Either way the new node has correct
frontmatter (id, kind=definition, status=staged, primary_topic,
topics) so the dependency graph is properly extended.

Generator discipline — but the goal is **stub quality good enough to
unblock the referring node**, not a polished definition. If the
agent can't even write a placeholder (e.g. the name is meaningless
nonsense), it returns ``cannot_stub``.
"""

from __future__ import annotations

from .decoder import (
    DefStubReview,
    DefStubReviewParseError,
    parse,
)
from .prompt import compose
from .role import AGENT_ROLE, DefStubGenerator

__all__ = [
    "AGENT_ROLE",
    "DefStubGenerator",
    "DefStubReview",
    "DefStubReviewParseError",
    "compose",
    "parse",
]
