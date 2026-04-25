"""
blackboard/auth.py — Authentication for CDU Learnline

Supports two modes:

1. INTERACTIVE (default / recommended)
   Opens a real visible browser → you log in exactly as you normally would
   on Learnline (SSO, MFA, etc.) → we detect success and capture the session.
   No credentials stored anywhere.

2. AUTO (optional, for hands-free re-login)
   Credentials saved securely in macOS Keychain via `keyring`.
   When your session expires the server re-logs in automatically.
   Set up by running: python3 setup.py

Cookies are cached to BB_SESSION_CACHE (default: ~/.bb_mcp_session.json)
so you stay logged in until the session expires server-side.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from config import settings

# ──────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────

KEYRING_SERVICE = "blackboard-mcp-cdu"
CACHE_PATH = Path(settings.session_cache).expanduser()

# Selectors that confirm we're on the Blackboard Ultra dashboard (logged in)
LOGGED_IN_SELECTORS = [
    "bb-base-layout",               # Blackboard Ultra root component
    "[data-testid='side-nav']",     # Ultra side navigation
    "[data-testid='base-layout']",  # Ultra base layout
    "ultra-landing-page",           # Ultra institution/landing page
    ".ultra-layout",
    "#ultra-landing-page",
    "bb-side-navigation",
    "[class*='ultra']",             # Any Ultra-specific class
    "[data-testid='global-nav']",
    # Classic Blackboard fallbacks
    "#globalNavPageNavArea",
    ".bb-offcanvas-nav",
    "#nav-bar",
    "#stream_container",
]

# URL fragments that indicate we're still on a login/SSO page
LOGIN_URL_PATTERNS = [
    "/webapps/login",
    "/auth/",
    "login.microsoftonline.com",
    "login.cdu.edu.au",
    "adfs/",
    "signin",
    "saml",
    "/cas/",
    "shibboleth",
]

# The post-login landing URL — depends on Blackboard interface version
def _get_landing_path() -> str:
    """
    Return the correct landing path for this university's Blackboard instance.
    Ultra:   /ultra/institution-page
    Classic: /webapps/portal/frameset.jsp
    Auto-detect by reading BB_INTERFACE from .env (set by setup.py).
    """
    interface = getattr(settings, "interface", "ultra").lower()
    if interface == "classic":
        return "/webapps/portal/frameset.jsp"
    return "/ultra/institution-page"   # default: Ultra


# ──────────────────────────────────────────────
#  Cookie cache (disk)
# ──────────────────────────────────────────────

def load_cached_cookies() -> dict[str, str] | None:
    """Load previously saved cookies, or None if missing/corrupt."""
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text())
        if isinstance(data, dict) and data:
            return data
    except Exception:
        pass
    return None


def save_cookies(cookies: dict[str, str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cookies, indent=2))


def clear_cookie_cache() -> None:
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
        print("[auth] Cookie cache cleared.", file=sys.stderr)


# ──────────────────────────────────────────────
#  macOS Keychain (optional auto-login)
# ──────────────────────────────────────────────

def save_credentials_to_keychain(username: str, password: str) -> bool:
    """Save CDU credentials to macOS Keychain. Returns True on success."""
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, "username", username)
        keyring.set_password(KEYRING_SERVICE, "password", password)
        return True
    except Exception as e:
        print(f"[auth] Could not save to Keychain: {e}", file=sys.stderr)
        return False


def load_credentials_from_keychain() -> tuple[str, str] | None:
    """Load saved credentials from macOS Keychain. Returns (username, password) or None."""
    try:
        import keyring
        username = keyring.get_password(KEYRING_SERVICE, "username")
        password = keyring.get_password(KEYRING_SERVICE, "password")
        if username and password:
            return username, password
    except Exception:
        pass
    return None


def delete_credentials_from_keychain() -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, "username")
        keyring.delete_password(KEYRING_SERVICE, "password")
        print("[auth] Keychain credentials removed.", file=sys.stderr)
    except Exception:
        pass


# ──────────────────────────────────────────────
#  Login detection helpers
# ──────────────────────────────────────────────

def _is_login_url(url: str) -> bool:
    """Return True if the URL looks like a login/SSO page."""
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in LOGIN_URL_PATTERNS)


def _is_learnline_url(url: str) -> bool:
    """Return True if we're back on the Learnline domain."""
    base = settings.base_url.split("//")[-1].split("/")[0]
    return base in url


async def _wait_for_login(page: Page, timeout_seconds: int = 180) -> bool:
    """
    Wait for the user to complete SSO login and land on Blackboard.
    Handles both Ultra (/ultra/) and Classic (/webapps/) interfaces.

    Strategy:
      Phase 1 — Wait for the browser to leave the university domain (redirect to SSO).
                 If SSO is embedded (no external redirect), skip after grace period.
      Phase 2 — Wait for the browser to return to Blackboard with real content.

    Returns True on success, False on timeout.
    """
    import time

    interface = getattr(settings, "interface", "ultra").lower()
    deadline = time.monotonic() + timeout_seconds
    left_home = False
    grace_deadline = time.monotonic() + 8

    print("[auth]    Phase 1: waiting for SSO redirect...", file=sys.stderr)

    while time.monotonic() < deadline:
        try:
            url = page.url
        except Exception:
            await asyncio.sleep(1)
            continue

        # ── Phase 1: detect leaving the university domain ─────────────────
        if not left_home:
            if not _is_learnline_url(url) or _is_login_url(url):
                left_home = True
                print(f"[auth]    Phase 1 ✓ SSO detected: {url[:80]}", file=sys.stderr)
                print("[auth]    Phase 2: waiting for you to finish logging in...", file=sys.stderr)
            elif time.monotonic() > grace_deadline:
                left_home = True
                print("[auth]    Phase 1: no external redirect — checking for login...", file=sys.stderr)
            else:
                await asyncio.sleep(0.8)
                continue

        # ── Phase 2: detect successful login on Blackboard ──────────────
        if _is_learnline_url(url) and not _is_login_url(url):
            # Ultra: URL contains /ultra/
            if interface != "classic" and "/ultra/" in url:
                await asyncio.sleep(2.5)
                try:
                    final_url = page.url
                    if "/ultra/" in final_url and not _is_login_url(final_url):
                        print("[auth]    Phase 2 ✓ Back on Ultra — login complete.", file=sys.stderr)
                        return True
                except Exception:
                    pass

            # Classic: URL contains /webapps/ and a known Blackboard element
            if interface == "classic" or "/ultra/" not in url:
                for sel in LOGGED_IN_SELECTORS:
                    try:
                        if await page.locator(sel).count() > 0:
                            print(f"[auth]    Phase 2 ✓ Element found: {sel}", file=sys.stderr)
                            return True
                    except Exception:
                        pass
                # Title fallback for Classic
                try:
                    title = await page.title()
                    if title and not any(
                        kw in title.lower()
                        for kw in ["login", "sign in", "log in", "authentication", "shibboleth"]
                    ):
                        print(f"[auth]    Phase 2 ✓ Page title OK: '{title}'", file=sys.stderr)
                        return True
                except Exception:
                    pass

        await asyncio.sleep(1.2)

    return False


# ──────────────────────────────────────────────
#  Interactive login (open browser, user logs in)
# ──────────────────────────────────────────────

async def interactive_login() -> dict[str, str]:
    """
    Open a visible browser → user logs in → capture session cookies.
    Works with any university SSO (Microsoft, Shibboleth, Google, custom).
    No credentials needed — just log in as you normally would.
    """
    target_url = f"{settings.base_url}{_get_landing_path()}"
    print(f"\n[auth] 🌐 Opening browser — navigating to {target_url}", file=sys.stderr)

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            headless=False,  # Always visible for interactive login
            args=["--start-maximized"],
        )
        context: BrowserContext = await browser.new_context(
            viewport=None,  # Use full window size
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page: Page = await context.new_page()

        # Go straight to the Ultra landing page — Blackboard will redirect to SSO if needed
        await page.goto(target_url, wait_until="domcontentloaded")

        print("[auth] ⏳ Waiting for you to log in... (you have 3 minutes)", file=sys.stderr)

        login_ok = await _wait_for_login(page, timeout_seconds=180)

        if not login_ok:
            await browser.close()
            raise RuntimeError(
                "Login timed out (3 minutes). "
                "Please run setup.py again and complete login within the time limit."
            )

        # Let the Ultra app fully load and set all its cookies
        await asyncio.sleep(2.5)

        cookies_raw = await context.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_raw}

        print(f"[auth] ✅ Login detected! Captured {len(cookies)} session cookies.", file=sys.stderr)

        await browser.close()

    save_cookies(cookies)
    return cookies


# ──────────────────────────────────────────────
#  Auto login (headless, using saved credentials)
# ──────────────────────────────────────────────

async def auto_login(username: str, password: str) -> dict[str, str]:
    """
    Headless login using saved credentials from macOS Keychain.
    Falls back to interactive_login() if automated login fails.
    """
    print("[auth] 🔄 Auto re-login with saved credentials...", file=sys.stderr)

    # Common username/password selectors for CDU SSO
    USERNAME_SELECTORS = [
        'input[name="username"]',
        'input[name="loginfmt"]',       # Azure AD
        'input[type="email"]',
        'input[id="username"]',
        'input[id="userNameInput"]',    # ADFS
        'input[name="UserName"]',
    ]
    PASSWORD_SELECTORS = [
        'input[name="password"]',
        'input[name="passwd"]',
        'input[type="password"]',
        'input[id="passwordInput"]',
        'input[name="Password"]',
    ]
    SUBMIT_SELECTORS = [
        'button[type="submit"]',
        'input[type="submit"]',
        'input[id="submitButton"]',
        'button[id="idSIButton9"]',
        '.btn-primary',
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(settings.base_url, wait_until="domcontentloaded")
        await asyncio.sleep(1.5)

        filled = False
        for attempt in range(3):
            for sel in USERNAME_SELECTORS:
                try:
                    elem = page.locator(sel).first
                    if await elem.is_visible(timeout=1500):
                        await elem.fill(username)
                        filled = True
                        break
                except Exception:
                    continue

            if filled:
                # Click next/submit (Azure AD shows password on next screen)
                for sel in SUBMIT_SELECTORS:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            break
                    except Exception:
                        continue
                await asyncio.sleep(1.5)

            # Fill password
            for sel in PASSWORD_SELECTORS:
                try:
                    elem = page.locator(sel).first
                    if await elem.is_visible(timeout=2000):
                        await elem.fill(password)
                        for s in SUBMIT_SELECTORS:
                            try:
                                btn = page.locator(s).first
                                if await btn.is_visible(timeout=1000):
                                    await btn.click()
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

            success = await _wait_for_login(page, timeout_seconds=20)
            if success:
                break

        cookies_raw = await context.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_raw}
        await browser.close()

    if not cookies or not _is_learnline_url(page.url):
        print("[auth] ⚠️ Auto-login failed, falling back to interactive login...", file=sys.stderr)
        return await interactive_login()

    save_cookies(cookies)
    print("[auth] ✅ Auto re-login successful.", file=sys.stderr)
    return cookies


# ──────────────────────────────────────────────
#  Main entry point used by the client
# ──────────────────────────────────────────────

async def get_cookies(force_refresh: bool = False) -> dict[str, str]:
    """
    Return valid session cookies using the best available method:

    1. Cached cookies (if still valid)
    2. Auto-login with Keychain credentials (if saved)
    3. Interactive browser login (always works)
    """
    if not force_refresh:
        cached = load_cached_cookies()
        if cached:
            print("[auth] 🍪 Using cached session.", file=sys.stderr)
            return cached

    # Try auto-login if credentials are saved in Keychain
    creds = load_credentials_from_keychain()
    if creds:
        username, password = creds
        print("[auth] 🔑 Found saved credentials in Keychain.", file=sys.stderr)
        return await auto_login(username, password)

    # Fall back to interactive browser login
    return await interactive_login()
