# 🎓 Blackboard MCP — CDU Learnline

An MCP server that connects Claude (or any MCP-compatible AI) to your **Charles Darwin University Learnline (Blackboard)** account. Instead of navigating Blackboard's complex interface, just ask Claude in plain English.

---

## What You Can Ask Claude

| Question | Tool used |
|---|---|
| "What courses am I enrolled in?" | `list_courses` |
| "Any new announcements?" | `get_announcements` |
| "What assignments are due this week?" | `get_due_dates` |
| "What's my grade in COMP101?" | `get_grades` |
| "Show me the content in Week 3" | `get_course_content` |
| "Catch me up on everything" | `summarize_activity` |
| "Tell me about my nursing unit" | `get_course_details` |

---

## Setup Guide

### Step 1 — Prerequisites

Your system needs Python 3.11+. The packages were installed under Python 3.13 (framework):
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 --version
```

### Step 2 — Install dependencies

```bash
cd "Blackboard MCP"
/Library/Frameworks/Python.framework/Versions/3.13/bin/pip3 install -r requirements.txt
```

Then install the Playwright browser (only needed once):
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/playwright install chromium
```

### Step 3 — Configure your credentials

Copy the example config and fill in your details:
```bash
cp .env.example .env
```

Edit `.env`:
```ini
BB_BASE_URL=https://learnline.cdu.edu.au
BB_USERNAME=your_student_number          # e.g. 12345678
BB_PASSWORD=your_learnline_password
BB_SESSION_CACHE=~/.bb_mcp_session.json
BB_HEADLESS=true
```

> ⚠️ **Never commit `.env` to git.** It contains your credentials.

### Step 4 — Test the login

Run this to verify your credentials work and cookies are cached:
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -c "
import asyncio
from blackboard.auth import login
asyncio.run(login())
print('Login successful!')
"
```

If your university uses **MFA / 2-Factor Authentication**, set `BB_HEADLESS=false` in `.env` first so you can see the browser and complete the verification.

### Step 5 — Configure Claude Desktop

Find your Claude Desktop config file:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add the following (replace the path with your actual project path):
```json
{
  "mcpServers": {
    "blackboard-cdu": {
      "command": "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
      "args": ["/Users/sasi/Blackboard MCP/server.py"],
      "cwd": "/Users/sasi/Blackboard MCP"
    }
  }
}
```

**Restart Claude Desktop.** You should see "blackboard-cdu" in the MCP tools list.

---

## Troubleshooting

### Login fails / can't find fields
Set `BB_HEADLESS=false` to watch the browser. The SSO login page selector may need adjustment if CDU changes their login page layout. Open an issue with a screenshot.

### Session expires frequently
The server automatically re-logs in when your session expires. If it's happening very often, check if CDU has short session timeouts.

### REST API returns 403
Some tools fall back to HTML scraping automatically. If you're consistently getting no data, the scraping selectors may need updating for your version of Blackboard.

### MFA / Authenticator app required
1. Set `BB_HEADLESS=false` in `.env`
2. Run `python -c "import asyncio; from blackboard.auth import login; asyncio.run(login())"`
3. Complete the MFA in the browser window that opens
4. Cookies are cached — you won't need to do this every time

---

## Project Structure

```
Blackboard MCP/
├── server.py              # MCP server — all tools registered here
├── config.py              # Settings loaded from .env
├── requirements.txt       # Python dependencies
├── .env.example           # Credential template (copy to .env)
├── README.md              # This file
│
└── blackboard/
    ├── auth.py            # Playwright SSO login + cookie caching
    ├── client.py          # HTTP client (REST API + HTML scraping fallback)
    └── models.py          # Pydantic data models
```

---

## Privacy & Security

- Your credentials are stored **only** in `.env` on your local machine
- Session cookies are cached in `~/.bb_mcp_session.json` (your home directory)
- All traffic goes directly between your machine and `learnline.cdu.edu.au`
- No data is sent to any third-party service

---

## Contributing

Found a bug or want to add a tool? PRs welcome! Common improvements:
- Better SSO selector detection for CDU's login page
- Support for downloading files
- Calendar integration for due dates
- Discussion board reading
