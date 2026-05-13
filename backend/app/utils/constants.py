"""
Project-wide constants.

Do not import these from anywhere other than this module.
If a value needs to change, change it here — it propagates automatically.
"""

# Tool names that must appear in evidence_from_tool for the grounding check to pass.
# Must stay in sync with the runner names used in app/tools/
#
# If a new language analyser is added, add its tool name(s) here.
# The guardrail grounding check imports this directly — no other copy exists.
VALID_TOOL_NAMES: frozenset[str] = frozenset({"bandit", "radon", "flake8", "eslint"})
