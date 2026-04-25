# Blackboard MCP — AI Install Prompts

Copy and paste the right prompt into your AI coding assistant.
The AI will install and configure everything for you automatically.

---

## Claude Code

```
Install the Blackboard MCP server for me from https://github.com/sasindudilshanranwadana/blackboard-mcp

Steps to follow:
1. Run this installer in the terminal: curl -fsSL https://raw.githubusercontent.com/sasindudilshanranwadana/blackboard-mcp/main/install.sh | bash
2. Wait for it to complete — it will open a browser for me to log in to my university Blackboard
3. After setup completes, confirm which MCP clients were configured
4. Tell me the exact query to test it with once I restart Claude Desktop

Do not ask me questions — just run the installer and report back what happened.
```

---

## Cursor / Windsurf / any AI with terminal access

```
I want to install the Blackboard MCP server so I can talk to my university's Blackboard LMS through AI.

Repository: https://github.com/sasindudilshanranwadana/blackboard-mcp

Please do the following in the terminal:
1. Run: curl -fsSL https://raw.githubusercontent.com/sasindudilshanranwadana/blackboard-mcp/main/install.sh | bash
2. The script is interactive — it will open a browser window for me to log in. Let it run.
3. After it finishes, show me what was installed and configured.
4. If anything fails, diagnose and fix it.

Start now.
```

---

## Manual fallback (if the AI can't run curl)

```
Help me install the Blackboard MCP server step by step.
Repo: https://github.com/sasindudilshanranwadana/blackboard-mcp

Run these commands one by one and tell me the output of each:
1. git clone https://github.com/sasindudilshanranwadana/blackboard-mcp.git ~/blackboard-mcp
2. cd ~/blackboard-mcp
3. python3 -m venv .venv && source .venv/bin/activate
4. pip install -r requirements.txt
5. playwright install chromium
6. python3 setup.py
```

---

## What happens after install

Once the setup wizard completes:

- **Claude Desktop** → restart the app, then ask: *"What courses am I enrolled in?"*
- **Claude Code** → the MCP server is already available in your current session
- **Cursor / Windsurf** → restart the app and the MCP server will be active

---

## Troubleshooting prompt

If something went wrong, paste this:

```
The Blackboard MCP server install at ~/blackboard-mcp failed or isn't working.
Repo: https://github.com/sasindudilshanranwadana/blackboard-mcp

Please:
1. Check if ~/blackboard-mcp exists
2. Check if ~/blackboard-mcp/.venv exists
3. Run: ~/blackboard-mcp/.venv/bin/python3 ~/blackboard-mcp/server.py and show me any errors
4. Check the MCP config file: cat ~/Library/Application\ Support/Claude/claude_desktop_config.json
5. Fix any issues you find
```
