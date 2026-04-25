# Contributing to Blackboard MCP

Thank you for helping make this better for students everywhere! 🎓

## Ways to contribute

- **Report bugs** — open an [issue](../../issues/new?template=bug_report.md)
- **Request features** — open an [issue](../../issues/new?template=feature_request.md)
- **Add university support** — if your uni needs a tweak, open a PR
- **Improve scraping fallbacks** — HTML structures vary across Blackboard versions

## Development setup

```bash
git clone https://github.com/sasinduranwadana/blackboard-mcp.git
cd blackboard-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python3 setup.py
```

## Project structure

```
server.py           ← MCP entry point — add new tools here
config.py           ← Settings (BB_BASE_URL, BB_INTERFACE, etc.)
setup.py            ← Interactive setup wizard
blackboard/
  auth.py           ← Browser login + cookie cache + Keychain
  client.py         ← HTTP client: REST API calls + HTML scraping
  models.py         ← Pydantic data models
  tools/            ← (future) split tools into modules
assets/             ← Images and static files
.github/            ← Issue templates, PR template, CI
```

## Adding a new MCP tool

1. Add a method to `blackboard/client.py` that returns a Pydantic model or list
2. Register it in `server.py` with `@mcp.tool()`
3. Return a plain string (Claude reads it as markdown)

Example:
```python
# server.py
@mcp.tool()
async def get_my_timetable(week: str = "current") -> str:
    """Get your class timetable. week: 'current' or 'next'."""
    client = await get_client()
    timetable = await client.get_timetable(week)
    if not timetable:
        return "No timetable found."
    return "\n".join(str(t) for t in timetable)
```

## Pull request checklist

- [ ] Tested with at least one real Blackboard instance
- [ ] No credentials or session data hardcoded
- [ ] New tools documented in README feature table
- [ ] `.env` changes reflected in `.env.example`

## Reporting security issues

Please **do not** open a public issue for security vulnerabilities.
See [SECURITY.md](SECURITY.md) instead.
