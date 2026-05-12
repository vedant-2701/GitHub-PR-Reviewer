## Session: [what you're building today]

### Done so far:
- [module]: [one line what it does, status]
- [module]: [one line what it does, status]

### Interfaces the new code must connect to:
[paste only the relevant schemas, function signatures, or class definitions]

### Starting now:
[exactly what we're building this session]

### Known constraints:
[anything specific to watch out for this session]

---

# Example 

## Session: Static analysis tools (complexity, security, syntax)

### Done so far:
- FastAPI scaffold: main.py, config.py, database.py — running
- /webhook endpoint: HMAC validation working, returns 200 on ping
- Diff parser: parse_pr() returns List[FileDiff] — tests passing

### Interfaces the new code must connect to:
class FileDiff(BaseModel):
    filename: str
    language: str
    added_line_numbers: List[int]
    raw_diff: str
    context_lines: str

### Starting now:
3 LangChain tools in app/tools/:
- complexity_tool.py (radon)
- security_tool.py (bandit)
- syntax_tool.py (ast)
Each returns structured JSON the agent will cite in evidence_from_tool.

### Known constraints:
- Tools must return typed JSON, not raw strings
- bandit and radon run via subprocess on temp files, not on live codebase