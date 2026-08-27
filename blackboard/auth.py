"""
blackboard/auth.py — Authentication for Blackboard MCP

Login flow:
  1. A browser opens automatically (Playwright's bundled Chromium)
  2. You log in as you normally would (SSO, MFA, saved passwords all work)
  3. The browser closes itself — cookies are captured automatically
  4. Session is cached so you only log in once per session lifetime

Cookies are cached to BB_SESSION_CACHE (default: ~/.bb_mcp_session.json).
"""
from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path

from config import settings

try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


# ──────────────────────────────────────────────
#  Errors
# ──────────────────────────────────────────────

class NotConfiguredError(Exception):
    """Raised when no Blackboard URL has been set yet."""


class LoginTimeoutError(Exception):
    """Raised when the browser login times out."""


# ──────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────

KEYRING_SERVICE = "blackboard-mcp"
CACHE_PATH = Path(settings.session_cache).expanduser()

LOGGED_IN_SELECTORS = [
    "bb-base-layout",
    "[data-testid='side-nav']",
    "[data-testid='base-layout']",
    "ultra-landing-page",
    ".ultra-layout",
    "#ultra-landing-page",
    "bb-side-navigation",
    "[class*='ultra']",
    "[data-testid='global-nav']",
    "#globalNavPageNavArea",
    ".bb-offcanvas-nav",
    "#nav-bar",
    "#stream_container",
]

LOGIN_URL_PATTERNS = [
    "/webapps/login",
    "/auth/",
    "login.microsoftonline.com",
    "adfs/",
    "signin",
    "saml",
    "/cas/",
    "shibboleth",
    "api-gateway",
]


def _get_landing_path() -> str:
    return "/webapps/login/"


# ──────────────────────────────────────────────
#  Cookie cache
# ──────────────────────────────────────────────

def filter_cookies(cookies: dict[str, str]) -> dict[str, str]:
    bb_keys = {'JSESSIONID', 'BbRouter', 'samlCookie', 'AWSALB', 'AWSALBCORS', 'BbClientCalenderTimeZone', 'XSRF-TOKEN', 's_session_id', 'session_id'}
    return {k: v for k, v in cookies.items() if k in bb_keys or 'bb' in k.lower() or 'blackboard' in k.lower()}


def load_cached_cookies() -> dict[str, str] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text())
        if isinstance(data, dict) and data:
            return filter_cookies(data)
    except Exception:
        pass
    return None


def save_cookies(cookies: dict[str, str]) -> None:
    cookies = filter_cookies(cookies)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cookies, indent=2))
    try:
        CACHE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def clear_cookie_cache() -> None:
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()


# ──────────────────────────────────────────────
#  OS Keychain (optional auto-login)
# ──────────────────────────────────────────────

def save_credentials_to_keychain(username: str, password: str) -> bool:
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, "username", username)
        keyring.set_password(KEYRING_SERVICE, "password", password)
        return True
    except Exception as e:
        print(f"[auth] Could not save to keychain: {e}", file=sys.stderr)
        return False


def load_credentials_from_keychain() -> tuple[str, str] | None:
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
    except Exception:
        pass


# ──────────────────────────────────────────────
#  Login detection
# ──────────────────────────────────────────────

def _is_login_url(url: str) -> bool:
    return any(p in url.lower() for p in LOGIN_URL_PATTERNS)


def _is_blackboard_url(url: str) -> bool:
    if not settings.base_url:
        return False
    base = settings.base_url.split("//")[-1].split("/")[0]
    return base in url


async def _wait_for_login(page: "Page", timeout_seconds: int = 180) -> bool:
    import time
    interface = getattr(settings, "interface", "ultra").lower()
    deadline = time.monotonic() + timeout_seconds
    left_home = False
    grace_deadline = time.monotonic() + 8

    while time.monotonic() < deadline:
        try:
            url = page.url
        except Exception:
            await asyncio.sleep(1)
            continue

        if not left_home:
            if not _is_blackboard_url(url) or _is_login_url(url):
                left_home = True
            elif time.monotonic() > grace_deadline:
                left_home = True
            else:
                await asyncio.sleep(0.8)
                continue

        if _is_blackboard_url(url) and not _is_login_url(url):
            if interface != "classic" and "/ultra/" in url:
                await asyncio.sleep(2.5)
                try:
                    final_url = page.url
                    if "/ultra/" in final_url and not _is_login_url(final_url):
                        return True
                except Exception:
                    pass

            if interface == "classic" or "/ultra/" not in url:
                for sel in LOGGED_IN_SELECTORS:
                    try:
                        if await page.locator(sel).count() > 0:
                            return True
                    except Exception:
                        pass

        await asyncio.sleep(1.2)

    return False


# ──────────────────────────────────────────────
#  Interactive login
# ──────────────────────────────────────────────

async def interactive_login(base_url: str | None = None) -> dict[str, str]:
    """
    Open Blackboard in a Playwright-controlled browser, wait for the
    student to log in (SSO/MFA all supported), capture cookies, close it.
    """
    target_base = base_url or settings.base_url
    if not target_base:
        raise NotConfiguredError(
            "No Blackboard URL is set. Please tell me your university's Blackboard URL first."
        )

    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "Playwright is required for login. Run:\n"
            "  pip install playwright && playwright install chromium"
        )

    target_url = f"{target_base}{_get_landing_path()}"
    print(f"\n[auth] Opening browser → {target_url}", file=sys.stderr)
    print("[auth] Log in as you normally would. You have 3 minutes.", file=sys.stderr)

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context: BrowserContext = await browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page: Page = await context.new_page()
        await page.goto(target_url, wait_until="domcontentloaded")

        ok = await _wait_for_login(page, timeout_seconds=180)
        if not ok:
            await browser.close()
            raise LoginTimeoutError(
                "Login timed out after 3 minutes. Please try again."
            )

        await asyncio.sleep(2.5)
        cookies_raw = await context.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_raw}
        await browser.close()

    if not cookies:
        raise LoginTimeoutError("No session cookies were captured.")

    save_cookies(cookies)
    print(f"[auth] ✅ Captured {len(cookies)} cookies.", file=sys.stderr)
    return cookies


# ──────────────────────────────────────────────
#  Main entry point used by the client
# ──────────────────────────────────────────────

async def get_cookies(force_refresh: bool = False) -> dict[str, str]:
    if not settings.is_configured():
        raise NotConfiguredError(
            "No Blackboard URL is configured. Please run 'Connect my Blackboard' first."
        )

    if not force_refresh:
        cached = load_cached_cookies()
        if cached:
            return cached

    return await interactive_login()
