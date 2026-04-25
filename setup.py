#!/usr/bin/env python3
"""
setup.py — One-time setup wizard for CDU Blackboard MCP

Run this once:
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 setup.py

What it does:
  1. Opens a real browser — you log in to Learnline as normal
  2. Captures your session automatically
  3. Tests the connection
  4. Optionally saves credentials to macOS Keychain for auto-relogin
  5. Auto-configures Claude Desktop
"""
from __future__ import annotations

import asyncio
import getpass
import json
import sys
from pathlib import Path

# ── pretty output via rich (bundled with mcp) ────────────────────────────────
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.text import Text
from rich import print as rprint

console = Console()

# ── project paths ─────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
PYTHON = sys.executable
CLAUDE_CONFIG_PATH = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
)

# ─────────────────────────────────────────────────────────────────────────────


def banner() -> None:
    console.print()
    console.print(Panel.fit(
        "[bold cyan]CDU Learnline MCP — Setup Wizard[/bold cyan]\n"
        "[dim]Connect Claude to your Blackboard account[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()


def step(n: int, total: int, title: str) -> None:
    console.print(Rule(f"[bold]Step {n}/{total}[/bold]  {title}", style="cyan"))
    console.print()


def success(msg: str) -> None:
    console.print(f"  [bold green]✅[/bold green] {msg}")


def info(msg: str) -> None:
    console.print(f"  [dim]ℹ[/dim]  {msg}")


def warn(msg: str) -> None:
    console.print(f"  [yellow]⚠[/yellow]  {msg}")


def error(msg: str) -> None:
    console.print(f"  [bold red]✗[/bold red]  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
#  Step 1 — Interactive browser login
# ─────────────────────────────────────────────────────────────────────────────

async def do_login() -> dict[str, str]:
    from blackboard.auth import interactive_login

    console.print("  A browser window will open — [bold]log in to Learnline as you normally would.[/bold]")
    console.print("  This works with CDU Single Sign-On and MFA.")
    console.print()
    info("Close the browser window [italic]after[/italic] you see your Learnline dashboard.")
    console.print()

    cookies = await interactive_login()
    return cookies


# ─────────────────────────────────────────────────────────────────────────────
#  Step 2 — Test connection
# ─────────────────────────────────────────────────────────────────────────────

async def do_test() -> bool:
    from blackboard.client import BlackboardClient

    info("Testing connection to Learnline REST API...")

    client = BlackboardClient()
    # Skip re-login during test — cookies were just captured
    from blackboard import auth
    client._cookies = auth.load_cached_cookies() or {}
    client._build_client()

    profile = await client.get_user_profile()
    if profile:
        success(f"Logged in as: [bold]{profile.full_name}[/bold]  (username: {profile.username})")
    else:
        warn("Could not fetch profile — session may use scraping fallback. This is OK.")

    courses = await client.get_courses()
    if courses:
        success(f"Found [bold]{len(courses)}[/bold] enrolled course(s):")
        for c in courses[:5]:
            console.print(f"     [cyan]•[/cyan] {c.name} [dim]({c.course_id})[/dim]")
        if len(courses) > 5:
            console.print(f"     [dim]  … and {len(courses) - 5} more[/dim]")
    else:
        warn("No courses found — you may need to check your Learnline enrolments.")

    await client.close()
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Step 3 — Keychain (optional auto-relogin)
# ─────────────────────────────────────────────────────────────────────────────

def do_keychain() -> None:
    console.print("  When your session expires, the server needs to log you in again.")
    console.print()
    console.print("  [bold]Option A[/bold] — [cyan]Save credentials in macOS Keychain[/cyan]")
    info("Fully automatic relogin. Credentials stored securely by macOS, not in any file.")
    console.print()
    console.print("  [bold]Option B[/bold] — [cyan]Re-run setup.py when needed[/cyan]")
    info("Just run setup.py again and log in via browser. Sessions typically last several days.")
    console.print()

    save = Confirm.ask("  Save credentials to macOS Keychain for automatic relogin?", default=True)

    if save:
        console.print()
        username = Prompt.ask("  [bold]CDU Student Number[/bold]", console=console)
        password = getpass.getpass("  CDU Password (hidden): ")

        from blackboard.auth import save_credentials_to_keychain
        ok = save_credentials_to_keychain(username.strip(), password.strip())
        if ok:
            success("Credentials saved to macOS Keychain (accessible only by you).")
            info("To remove them later, run:  python3 setup.py --clear-keychain")
        else:
            warn("Keychain save failed. You can re-run setup.py when your session expires.")
    else:
        info("No problem — run  [bold]python3 setup.py[/bold]  again when your session expires.")


# ─────────────────────────────────────────────────────────────────────────────
#  Step 4 — Configure Claude Desktop
# ─────────────────────────────────────────────────────────────────────────────

def do_claude_config() -> None:
    server_path = str(PROJECT_DIR / "server.py")
    entry = {
        "command": PYTHON,
        "args": [server_path],
        "cwd": str(PROJECT_DIR),
    }

    # Read existing config (or start fresh)
    config: dict = {}
    if CLAUDE_CONFIG_PATH.exists():
        try:
            config = json.loads(CLAUDE_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            config = {}

    already_configured = "blackboard-cdu" in config.get("mcpServers", {})

    config.setdefault("mcpServers", {})["blackboard-cdu"] = entry
    CLAUDE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")

    if already_configured:
        success("Claude Desktop config [bold]updated[/bold].")
    else:
        success("Claude Desktop config [bold]created[/bold].")

    info(f"Config file: [dim]{CLAUDE_CONFIG_PATH}[/dim]")
    console.print()
    warn("[bold]Restart Claude Desktop[/bold] to activate the MCP server.")


# ─────────────────────────────────────────────────────────────────────────────
#  Utility: clear everything
# ─────────────────────────────────────────────────────────────────────────────

def handle_clear_keychain() -> None:
    from blackboard.auth import delete_credentials_from_keychain, clear_cookie_cache
    banner()
    console.print("[bold red]Clearing saved data...[/bold red]")
    console.print()
    delete_credentials_from_keychain()
    clear_cookie_cache()
    success("Keychain credentials removed.")
    success("Session cache cleared.")
    console.print()
    info("Run [bold]python3 setup.py[/bold] to set up again.")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    # Handle flags
    if "--clear-keychain" in sys.argv or "--reset" in sys.argv:
        handle_clear_keychain()
        return

    banner()

    total_steps = 4

    # ── Step 1: Login ───────────────────────────────────────────────────────
    step(1, total_steps, "Log in to CDU Learnline")
    try:
        await do_login()
        success("Session captured and cached.")
    except Exception as e:
        error(f"Login failed: {e}")
        console.print()
        info("Make sure you're connected to the internet and CDU Learnline is accessible.")
        sys.exit(1)

    console.print()

    # ── Step 2: Test ────────────────────────────────────────────────────────
    step(2, total_steps, "Test your connection")
    try:
        await do_test()
    except Exception as e:
        warn(f"Connection test had issues: {e}")
        info("The server may still work — continuing setup.")

    console.print()

    # ── Step 3: Keychain ────────────────────────────────────────────────────
    step(3, total_steps, "Auto-relogin (optional)")
    do_keychain()

    console.print()

    # ── Step 4: Claude Desktop ──────────────────────────────────────────────
    step(4, total_steps, "Configure Claude Desktop")
    do_claude_config()

    console.print()

    # ── Done ────────────────────────────────────────────────────────────────
    console.print(Panel.fit(
        "[bold green]🎉  All done! Blackboard MCP is ready.[/bold green]\n\n"
        "[bold]Restart Claude Desktop, then try asking:[/bold]\n\n"
        '  [cyan]"What courses am I enrolled in?"[/cyan]\n'
        '  [cyan]"What assignments are due this week?"[/cyan]\n'
        '  [cyan]"Catch me up on everything in Learnline"[/cyan]',
        border_style="green",
        padding=(1, 4),
    ))
    console.print()


if __name__ == "__main__":
    asyncio.run(main())
