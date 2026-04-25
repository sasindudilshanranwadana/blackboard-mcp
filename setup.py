#!/usr/bin/env python3
"""
setup.py — One-time setup wizard for Blackboard MCP

Works with ANY university that uses Blackboard Learn (Ultra or Classic).

Run this once:
    python3 setup.py

What it does:
  1. Asks for your university's Blackboard URL
  2. Auto-detects Blackboard Ultra vs Classic interface
  3. Opens a real browser — you log in as normal (works with any SSO / MFA)
  4. Tests the connection and lists your courses
  5. Optionally saves credentials to macOS Keychain for auto-relogin
  6. Auto-configures Claude Desktop
"""
from __future__ import annotations

import asyncio
import getpass
import json
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule

console = Console()

PROJECT_DIR = Path(__file__).parent
PYTHON = sys.executable
ENV_FILE = PROJECT_DIR / ".env"

# ── Known university Blackboard URLs (for autocomplete hints) ─────────────────
KNOWN_UNIVERSITIES: dict[str, str] = {
    "cdu":        "https://online.cdu.edu.au",
    "uq":         "https://learn.uq.edu.au",
    "unsw":       "https://moodle.telt.unsw.edu.au",   # UNSW uses Moodle
    "uwa":        "https://lms.uwa.edu.au",
    "anu":        "https://wattlecourses.anu.edu.au",
    "usyd":       "https://canvas.sydney.edu.au",       # Sydney uses Canvas
    "deakin":     "https://d2l.deakin.edu.au",
    "latrobe":    "https://latrobe.blackboard.com",
    "federation": "https://federation.edu.au/blackboard",
}

# ─────────────────────────────────────────────────────────────────────────────


def banner() -> None:
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Blackboard MCP — Setup Wizard[/bold cyan]\n"
        "[dim]Connect Claude to your university Blackboard account[/dim]",
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


def error_msg(msg: str) -> None:
    console.print(f"  [bold red]✗[/bold red]  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
#  Step 1 — University URL + interface detection
# ─────────────────────────────────────────────────────────────────────────────

async def do_university_setup() -> tuple[str, str]:
    """
    Ask for the Blackboard URL, detect interface (Ultra/Classic), save to .env.
    Returns (base_url, interface) where interface is 'ultra' or 'classic'.
    """
    # Check if already configured
    existing_url = _read_env_value("BB_BASE_URL")
    if existing_url:
        console.print(f"  Current Blackboard URL: [cyan]{existing_url}[/cyan]")
        change = Confirm.ask("  Change it?", default=False)
        if not change:
            interface = _read_env_value("BB_INTERFACE") or "ultra"
            return existing_url, interface

    console.print("  Enter your university's Blackboard URL.")
    console.print("  [dim]Examples:  https://online.cdu.edu.au  |  https://blackboard.myuni.edu.au[/dim]")
    console.print()

    while True:
        raw = Prompt.ask("  [bold]Blackboard URL[/bold]").strip().rstrip("/")
        if not raw.startswith("http"):
            raw = "https://" + raw
        # Basic URL validation
        if re.match(r"https?://[a-zA-Z0-9.\-]+", raw):
            base_url = raw
            break
        warn("That doesn't look like a valid URL. Please try again.")

    console.print()
    info(f"Detecting Blackboard interface at [cyan]{base_url}[/cyan] ...")

    interface = await _detect_interface(base_url)
    if interface == "ultra":
        success("Blackboard [bold]Ultra[/bold] interface detected 🎨")
    else:
        success("Blackboard [bold]Classic[/bold] interface detected 📚")

    # Save to .env
    _write_env({"BB_BASE_URL": base_url, "BB_INTERFACE": interface})
    info(f"Saved to [dim]{ENV_FILE}[/dim]")

    return base_url, interface


async def _detect_interface(base_url: str) -> str:
    """
    Try loading the Ultra landing page — if it responds with Ultra content, return 'ultra'.
    Otherwise return 'classic'.
    """
    import httpx
    ultra_url = f"{base_url}/ultra/institution-page"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(ultra_url)
            final = str(resp.url)
            if "/ultra/" in final and base_url.split("//")[-1].split("/")[0] in final:
                return "ultra"
    except Exception:
        pass
    return "classic"


def _read_env_value(key: str) -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def _write_env(values: dict[str, str]) -> None:
    """Write/update key=value pairs in the .env file."""
    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
    existing.update(values)
    lines = [f"{k}={v}" for k, v in existing.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
#  Step 2 — Interactive browser login
# ─────────────────────────────────────────────────────────────────────────────

async def do_login(interface: str) -> dict[str, str]:
    # Reload config after .env was written
    import importlib

    import config as cfg_module
    importlib.reload(cfg_module)
    import blackboard.auth as auth_module
    importlib.reload(auth_module)

    from blackboard.auth import interactive_login

    console.print("  A browser window will open — [bold]log in as you normally would.[/bold]")
    console.print("  Works with any SSO, Microsoft, Shibboleth, Google, or MFA.")
    console.print()
    info("Complete the login in the browser. The wizard will detect it automatically.")
    console.print()

    cookies = await interactive_login()
    return cookies


# ─────────────────────────────────────────────────────────────────────────────
#  Step 3 — Test connection
# ─────────────────────────────────────────────────────────────────────────────

async def do_test() -> bool:
    import importlib

    import blackboard.auth as auth_module
    import blackboard.client as client_module
    importlib.reload(client_module)

    from blackboard.client import BlackboardClient

    info("Testing connection to Blackboard REST API...")

    client = BlackboardClient()
    client._cookies = auth_module.load_cached_cookies() or {}
    client._build_client()

    profile = await client.get_user_profile()
    if profile:
        success(f"Logged in as: [bold]{profile.full_name}[/bold]  (id: {profile.username})")
    else:
        warn("Could not fetch profile — REST API may need admin approval at your university.")
        info("The server will fall back to web scraping. Most features will still work.")

    courses = await client.get_courses()
    if courses:
        success(f"Found [bold]{len(courses)}[/bold] enrolled course(s):")
        for c in courses[:5]:
            console.print(f"     [cyan]•[/cyan] {c.name} [dim]({c.course_id})[/dim]")
        if len(courses) > 5:
            console.print(f"     [dim]  … and {len(courses) - 5} more[/dim]")
    else:
        warn("No courses found. Check your enrolments on the Blackboard website.")

    await client.close()
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Step 4 — Keychain (optional auto-relogin)
# ─────────────────────────────────────────────────────────────────────────────

def do_keychain() -> None:
    console.print("  You already logged in via the browser above. \u2705")
    console.print()
    console.print("  University sessions expire every few days. When that happens you have two options:")
    console.print()
    console.print("  [bold]Option A[/bold] — [green]Browser re-opens automatically[/green] [dim](recommended \u2014 no password needed)[/dim]")
    info("When your session expires, a browser window will open and you log in again.")
    info("Nothing is stored. Simple and secure.")
    console.print()
    console.print("  [bold]Option B[/bold] — [cyan]Save password in macOS Keychain[/cyan]")
    info("Fully silent background relogin \u2014 no browser popup ever.")
    info("Password is stored by macOS Keychain, not in any file.")
    console.print()

    save = Confirm.ask(
        "  Save password to Keychain for fully silent relogin? (No = browser reopens when needed)",
        default=False,
    )

    if save:
        console.print()
        username = Prompt.ask("  [bold]Username / Student Number[/bold]")
        password = getpass.getpass("  Password (hidden): ")

        from blackboard.auth import save_credentials_to_keychain
        ok = save_credentials_to_keychain(username.strip(), password.strip())
        if ok:
            success("Password saved to macOS Keychain \u2014 relogin will be fully automatic.")
            info("To remove it later:  python3 setup.py --reset")
        else:
            warn("Keychain save failed \u2014 browser will reopen when your session expires.")
    else:
        success("No problem \u2014 a browser window will open automatically when your session expires.")


# ─────────────────────────────────────────────────────────────────────────────
#  Step 5 — Configure Claude Desktop
# ─────────────────────────────────────────────────────────────────────────────

# Known MCP client config file locations on macOS
MCP_CLIENTS: list[tuple[str, Path]] = [
    (
        "Claude Desktop",
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    ),
    (
        "Claude Code",
        Path.home() / ".claude" / "claude_desktop_config.json",
    ),
    (
        "Cursor",
        Path.home() / ".cursor" / "mcp.json",
    ),
    (
        "Windsurf",
        Path.home() / ".codeium" / "windsurf" / "mcp_config.json",
    ),
]


def do_claude_config(base_url: str) -> None:
    # Derive a nice server name from the URL (e.g. "blackboard-cdu", "blackboard-uq")
    hostname = base_url.split("//")[-1].split("/")[0]          # e.g. online.cdu.edu.au
    parts = hostname.replace("www.", "").split(".")
    uni_slug = next(
        (p for p in reversed(parts) if p not in ("edu", "au", "com", "ac", "uk", "nz", "online", "learn", "lms", "bb")),
        parts[0],
    )
    server_name = f"blackboard-{uni_slug}"

    entry = {
        "command": PYTHON,
        "args": [str(PROJECT_DIR / "server.py")],
        "cwd": str(PROJECT_DIR),
    }

    configured: list[str] = []
    skipped: list[str] = []

    for client_name, config_path in MCP_CLIENTS:
        # Only configure clients that are actually installed (config dir exists)
        if not config_path.parent.exists():
            skipped.append(client_name)
            continue

        config: dict = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                config = {}

        config.setdefault("mcpServers", {})[server_name] = entry
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        configured.append(client_name)

    if configured:
        success(f"Server [cyan]{server_name}[/cyan] configured in: {', '.join(configured)}")
    if skipped:
        info(f"Not installed (skipped): {', '.join(skipped)}")
    console.print()
    warn("[bold]Restart any configured apps[/bold] to activate the MCP server.")


# ─────────────────────────────────────────────────────────────────────────────
#  Utility flags
# ─────────────────────────────────────────────────────────────────────────────

def handle_clear_keychain() -> None:
    from blackboard.auth import clear_cookie_cache, delete_credentials_from_keychain
    banner()
    console.print("[bold red]Clearing saved data...[/bold red]\n")
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
    if "--clear-keychain" in sys.argv or "--reset" in sys.argv:
        handle_clear_keychain()
        return

    banner()

    total_steps = 5

    # ── Step 1: University URL ───────────────────────────────────────────────
    step(1, total_steps, "Your University Blackboard URL")
    try:
        base_url, interface = await do_university_setup()
    except Exception as e:
        error_msg(f"URL setup failed: {e}")
        sys.exit(1)

    console.print()

    # ── Step 2: Login ────────────────────────────────────────────────────────
    step(2, total_steps, "Log in to Blackboard")
    try:
        await do_login(interface)
        success("Session captured and cached.")
    except Exception as e:
        error_msg(f"Login failed: {e}")
        info("Make sure you're connected to the internet and your Blackboard URL is correct.")
        sys.exit(1)

    console.print()

    # ── Step 3: Test ─────────────────────────────────────────────────────────
    step(3, total_steps, "Test your connection")
    try:
        await do_test()
    except Exception as e:
        warn(f"Connection test had issues: {e}")
        info("The server may still work — continuing setup.")

    console.print()

    # ── Step 4: Keychain ─────────────────────────────────────────────────────
    step(4, total_steps, "Auto-relogin (optional)")
    do_keychain()

    console.print()

    # ── Step 5: Claude Desktop ───────────────────────────────────────────────
    step(5, total_steps, "Configure Claude Desktop")
    do_claude_config(base_url)

    console.print()

    # ── Done ─────────────────────────────────────────────────────────────────
    console.print(Panel.fit(
        "[bold green]🎉  All done! Blackboard MCP is ready.[/bold green]\n\n"
        "[bold]Restart Claude Desktop, then try asking:[/bold]\n\n"
        '  [cyan]"What courses am I enrolled in?"[/cyan]\n'
        '  [cyan]"What assignments are due this week?"[/cyan]\n'
        '  [cyan]"Catch me up on everything in Blackboard"[/cyan]',
        border_style="green",
        padding=(1, 4),
    ))
    console.print()


if __name__ == "__main__":
    asyncio.run(main())
