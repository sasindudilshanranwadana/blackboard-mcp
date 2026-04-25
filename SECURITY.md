# Security Policy

## What is and isn't in this repository

| File | In repo? | Why |
|------|----------|-----|
| `.env.example` | ✅ Yes | Template only — no real credentials |
| `.env` | ❌ No | Contains your credentials — gitignored |
| `~/.bb_mcp_session.json` | ❌ No | Session cookies — stored in home dir, outside the repo |
| macOS Keychain entries | ❌ No | Stored by the OS, never written to disk in this project |

## Sensitive data this project handles

1. **CDU student credentials** — entered once during `setup.py`, optionally saved to macOS Keychain. Never written to any file inside the project directory.
2. **Session cookies** — captured by Playwright after login, cached at `~/.bb_mcp_session.json` (your home directory, not the repo). They expire server-side and are refreshed automatically.
3. **Blackboard API responses** — course names, grades, announcements — only ever held in memory during a tool call and returned to your local Claude client. Nothing is logged to disk.

## Reporting a vulnerability

If you find a security issue (e.g. credential leak, path traversal, unsafe input handling), please **do not open a public issue**. Instead:

- Open a **private GitHub Security Advisory** on this repo, or
- Email the maintainer directly

## Security best practices for users

- **Never commit `.env`** — it's in `.gitignore` but double-check with `git status` before pushing.
- **Rotate your CDU password** if you ever accidentally paste it somewhere (chat, terminal history visible on screen, etc.). Change it at [https://portal.cdu.edu.au](https://portal.cdu.edu.au).
- **Clear terminal history** that may contain your password: `history -c` in zsh.
- **Run with least privilege** — the MCP server only needs read access to Blackboard; avoid giving it any write/admin scopes.
- **Keep dependencies updated** — run `pip install -r requirements.txt --upgrade` periodically.

## What the server does NOT do

- Does not send your data to any third-party service
- Does not log credentials or cookies to stdout/files
- Does not make any network requests outside of `online.cdu.edu.au` and Microsoft SSO
- Does not store anything in the project directory at runtime
