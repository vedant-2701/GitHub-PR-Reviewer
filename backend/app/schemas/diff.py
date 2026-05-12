"""
FileDiff schema — the output contract of diff_parser.py.

Consumed by:
  - app/services/guardrail.py  (line number validation uses added_line_numbers)
  - app/services/review_agent.py  (raw_diff + context_lines sent to LLM)
  - app/tasks/review_task.py  (iterates List[FileDiff])

Do not add fields here without updating all three consumers.
"""
from typing import List
from pydantic import BaseModel


class FileDiff(BaseModel):
    filename: str
    language: str  # "python" | "javascript" | "typescript" | "unknown"
    added_line_numbers: List[int]  # "+" lines only — used by guardrail line validation
    raw_diff: str  # full unified diff text for this file
    context_lines: str  # ±20 lines around changed hunks, merged if close, from file_content