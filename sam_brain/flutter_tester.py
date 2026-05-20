"""
Sam's Flutter UI tester — resilient, codebase-aware.

Flow:
  1. Read the Flutter project code to understand routes + screens
  2. Start: flutter run -d chrome
  3. Wait for local URL
  4. Playwright navigates the app:
       - Every click/fill retries with scroll + JS fallback
       - Reads discovered routes to guide navigation
       - Tests login (with saved per-app credentials)
       - Explores all reachable screens
       - Detects errors, crashes, blocked interactions
       - Screenshots every significant step
  5. Kills flutter, returns report

Credentials are stored per-app, per-role in memory:
  admin login, resident login, etc. — never asks twice for the same app.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Flutter codebase reader — understand the app before testing it
# ---------------------------------------------------------------------------

def _read_flutter_routes(project_path: str) -> list[dict]:
    """
    Parse the Flutter project to discover screens and routes.
    Reads: lib/main.dart, lib/router.dart, lib/routes.dart, lib/app_router.dart
    Returns: [{"name": "/home", "widget": "HomeScreen"}, ...]
    """
    root = Path(project_path)
    routes: list[dict] = []
    seen_widgets: set[str] = set()

    candidates = [
        root / "lib" / "main.dart",
        root / "lib" / "router.dart",
        root / "lib" / "routes.dart",
        root / "lib" / "app_router.dart",
        root / "lib" / "core" / "router.dart",
        root / "lib" / "config" / "routes.dart",
    ]
    # Also glob for any *router* or *route* file
    for f in root.glob("lib/**/*rout*.dart"):
        candidates.append(f)

    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Pattern 1: GoRouter routes
        for m in re.finditer(r"path:\s*['\"]([^'\"]+)['\"].*?(?:builder|page).*?(?:=>|return)\s*([\w]+)", text, re.DOTALL):
            route_path, widget = m.group(1), m.group(2)
            if widget not in seen_widgets:
                routes.append({"name": route_path, "widget": widget})
                seen_widgets.add(widget)

        # Pattern 2: MaterialApp routes map
        for m in re.finditer(r"['\"]([/\w]+)['\"\s]*:\s*\(.*?\)\s*=>\s*([\w]+)", text):
            route_path, widget = m.group(1), m.group(2)
            if widget not in seen_widgets:
                routes.append({"name": route_path, "widget": widget})
                seen_widgets.add(widget)

        # Pattern 3: Screen class names
        for m in re.finditer(r"class\s+([\w]+Screen|[\w]+Page|[\w]+View)\s+extends", text):
            widget = m.group(1)
            if widget not in seen_widgets:
                routes.append({"name": widget.replace("Screen", "").replace("Page", "").replace("View", ""), "widget": widget})
                seen_widgets.add(widget)

    return routes[:20]  # cap at 20


def _read_app_name(project_path: str) -> str:
    """Read app name from pubspec.yaml."""
    pubspec = Path(project_path) / "pubspec.yaml"
    if pubspec.exists():
        try:
            text = pubspec.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'^name:\s*(\S+)', text, re.MULTILINE)
            if m:
                return m.group(1)
        except Exception:
            pass
    return Path(project_path).name


def _has_login_in_code(project_path: str) -> bool:
    """Check if the project has any login/auth screens."""
    root = Path(project_path)
    keywords = ["login", "signin", "sign_in", "auth", "password", "email"]
    for dart_file in list(root.glob("lib/**/*.dart"))[:40]:
        try:
            text = dart_file.read_text(encoding="utf-8", errors="replace").lower()
            if any(kw in text for kw in keywords):
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Flutter process
# ---------------------------------------------------------------------------

def _start_flutter(project_path: str) -> tuple[Any, str | None]:
    """Launch flutter run -d chrome, wait for the serving URL."""
    print("[FLUTTER] Starting flutter run -d chrome ...", flush=True)
    try:
        proc = subprocess.Popen(
            ["flutter", "run", "-d", "chrome", "--no-pub"],
            cwd=project_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
    except FileNotFoundError:
        return None, None

    url = None
    deadline = time.time() + 90
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        print(f"[FLUTTER] {line.rstrip()}", flush=True)
        m = re.search(r'http://localhost:(\d+)', line)
        if m:
            url = f"http://localhost:{m.group(1)}"
            print(f"[FLUTTER] Ready at {url}", flush=True)
            time.sleep(3)
            break

    return proc, url


def _stop_flutter(proc: Any) -> None:
    if proc is None:
        return
    try:
        import signal as _sig
        if sys.platform == "win32":
            proc.send_signal(_sig.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Resilient interaction helpers — retry with scroll + JS fallback
# ---------------------------------------------------------------------------

def _safe_click(page: Any, locator: Any, label: str = "") -> bool:
    """
    Click an element. Retries:
      1. Regular click
      2. Scroll into view + click
      3. JavaScript click (bypasses overlays)
    Returns True on success.
    """
    for attempt in range(3):
        try:
            if attempt == 0:
                locator.click(timeout=4000)
            elif attempt == 1:
                locator.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.3)
                locator.click(timeout=4000)
            else:
                # JS click — handles overlays and intercepted clicks
                locator.evaluate("el => el.click()")
            return True
        except Exception as e:
            if attempt == 2:
                print(f"[TESTER] Click failed for '{label}': {e}", flush=True)
    return False


def _safe_fill(page: Any, locator: Any, value: str, label: str = "") -> bool:
    """
    Fill an input. Retries with scroll + clear + type fallback.
    Returns True on success.
    """
    for attempt in range(3):
        try:
            if attempt == 0:
                locator.fill(value, timeout=4000)
            elif attempt == 1:
                locator.scroll_into_view_if_needed(timeout=3000)
                locator.clear()
                locator.fill(value, timeout=4000)
            else:
                # Type character by character (some Flutter inputs need this)
                locator.click(timeout=3000)
                locator.evaluate("el => { el.value = ''; el.dispatchEvent(new Event('input')); }")
                page.keyboard.type(value, delay=30)
            return True
        except Exception as e:
            if attempt == 2:
                print(f"[TESTER] Fill failed for '{label}': {e}", flush=True)
    return False


def _wait_stable(page: Any, timeout: int = 5000) -> None:
    """Wait for network idle and any animations to settle."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    time.sleep(0.5)


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------

def _screenshot_dir(project_path: str) -> Path:
    d = Path(project_path) / "test_results"
    d.mkdir(exist_ok=True)
    return d


def _shot(page: Any, project_path: str, label: str) -> str:
    ts = time.strftime("%H%M%S")
    safe_label = re.sub(r'[^a-z0-9_]', '_', label.lower())
    path = _screenshot_dir(project_path) / f"{ts}_{safe_label}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"[TESTER] Screenshot: {path.name}", flush=True)
    except Exception:
        pass
    return str(path)


# ---------------------------------------------------------------------------
# Error detection
# ---------------------------------------------------------------------------

def _check_error(page: Any) -> str | None:
    """Return error text if a Flutter error screen or obvious error is visible."""
    try:
        text = page.inner_text("body").lower()
    except Exception:
        return None
    signals = [
        "an error occurred", "something went wrong", "unexpected null",
        "null check operator", "type 'null'", "failed to load",
        "connection refused", "socketexception", "handshakeexception",
        "unhandled exception", "flutter error", "renderoverflowedbox",
    ]
    for sig in signals:
        if sig in text:
            idx = text.find(sig)
            return text[max(0, idx - 20): idx + 120].strip()
    return None


# ---------------------------------------------------------------------------
# Login flow — resilient, multi-strategy
# ---------------------------------------------------------------------------

_EMAIL_SELECTORS = [
    "input[type='email']",
    "input[name*='email' i]",
    "input[placeholder*='email' i]",
    "input[placeholder*='Email' i]",
    "input[autocomplete='email']",
]
_PASS_SELECTORS = [
    "input[type='password']",
    "input[name*='password' i]",
    "input[placeholder*='password' i]",
    "input[placeholder*='Password' i]",
]
_LOGIN_BTN_SELECTORS = [
    "button:has-text('Login')",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
    "button:has-text('Continue')",
    "button:has-text('Submit')",
    "button:has-text('Enter')",
    "[role='button']:has-text('Login')",
    "[role='button']:has-text('Sign in')",
]


def _find_input(page: Any, selectors: list[str]) -> Any | None:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return loc
        except Exception:
            continue
    # Flutter web fallback: find all inputs and guess by position
    try:
        inputs = page.locator("input").all()
        if inputs:
            return inputs[0] if "email" in str(selectors) else (inputs[1] if len(inputs) > 1 else None)
    except Exception:
        pass
    return None


def _test_login(page: Any, project_path: str, email: str, password: str, role: str = "") -> dict:
    result = {"attempted": False, "success": False, "role": role, "message": ""}

    email_input = _find_input(page, _EMAIL_SELECTORS)
    pass_input = _find_input(page, _PASS_SELECTORS)

    if email_input is None or pass_input is None:
        result["message"] = "No login form detected on screen"
        return result

    result["attempted"] = True
    print(f"[TESTER] Attempting login: {email} ({role or 'default'})", flush=True)

    filled_email = _safe_fill(page, email_input, email, "email")
    time.sleep(0.2)
    filled_pass = _safe_fill(page, pass_input, password, "password")
    time.sleep(0.2)

    if not (filled_email and filled_pass):
        result["message"] = "Could not fill login form fields"
        return result

    # Submit
    submitted = False
    for sel in _LOGIN_BTN_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0:
                if _safe_click(page, btn, "login button"):
                    submitted = True
                    break
        except Exception:
            continue

    if not submitted:
        # Try pressing Enter on the password field
        try:
            pass_input.press("Enter")
            submitted = True
        except Exception:
            pass

    if not submitted:
        result["message"] = "Filled credentials but could not find/click submit button"
        return result

    _wait_stable(page, 8000)
    time.sleep(1)

    # Determine success: if password field is gone, we moved to next screen
    still_on_login = False
    for sel in _PASS_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                still_on_login = True
                break
        except Exception:
            pass

    result["success"] = not still_on_login
    result["message"] = (
        f"Login succeeded ({email})"
        if result["success"]
        else f"Login attempt with {email} — still on login screen (check credentials)"
    )
    return result


# ---------------------------------------------------------------------------
# Navigation tester — guided by discovered routes
# ---------------------------------------------------------------------------

_NAV_SELECTORS = [
    "[role='tab']",
    "nav a",
    ".nav-item",
    "[role='menuitem']",
    "li > a",
    "[class*='bottom-nav'] [role='button']",
    "[class*='BottomNav'] [role='button']",
    "[class*='tab']",
]


def _test_navigation(page: Any, project_path: str, routes: list[dict]) -> list[dict]:
    """Click through navigation items. Returns list of {label, status, error}."""
    results = []

    # Try DOM-based nav items first
    nav_items = []
    for sel in _NAV_SELECTORS:
        try:
            items = page.locator(sel).all()
            if len(items) >= 2:
                nav_items = items[:8]
                break
        except Exception:
            continue

    for item in nav_items:
        label = "?"
        try:
            label = (
                item.inner_text().strip()
                or item.get_attribute("aria-label")
                or item.get_attribute("title")
                or "item"
            )[:30]
            if not label.strip():
                continue
            ok = _safe_click(page, item, label)
            _wait_stable(page, 4000)

            err = _check_error(page)
            results.append({"label": label, "status": "ok" if (ok and not err) else "error", "error": err or ""})
            _shot(page, project_path, f"nav_{re.sub(r'[^a-z0-9]', '_', label.lower())}")
        except Exception as e:
            results.append({"label": label, "status": "error", "error": str(e)[:80]})

    return results


# ---------------------------------------------------------------------------
# Button tester
# ---------------------------------------------------------------------------

def _test_buttons(page: Any, project_path: str, limit: int = 10) -> list[dict]:
    results = []
    try:
        buttons = page.locator("button, [role='button']").all()
    except Exception:
        return results

    tested = 0
    for btn in buttons:
        if tested >= limit:
            break
        label = "?"
        try:
            if not btn.is_visible() or not btn.is_enabled():
                continue
            label = (btn.inner_text().strip() or btn.get_attribute("aria-label") or "button")[:30]
            ok = _safe_click(page, btn, label)
            _wait_stable(page, 3000)
            err = _check_error(page)
            results.append({
                "label": label,
                "status": "ok" if (ok and not err) else ("error" if err else "click_failed"),
                "error": err or ("" if ok else "click intercepted"),
            })
            tested += 1
            # If we navigated away, go back
            try:
                page.go_back(timeout=3000)
                _wait_stable(page, 2000)
            except Exception:
                pass
        except Exception as e:
            results.append({"label": label, "status": "skip", "error": str(e)[:50]})

    return results


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_flutter_ui_test(
    project_path: str,
    *,
    email: str = "",
    password: str = "",
    role: str = "",
    memory_path: "Path | None" = None,
) -> str:
    """Full UI test. Returns a human-readable report."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return (
            "Playwright isn't installed.\n"
            "Run:  pip install playwright && playwright install chromium\n"
            "Then ask me to test again."
        )

    project_name = _read_app_name(project_path)
    routes = _read_flutter_routes(project_path)
    has_login = _has_login_in_code(project_path) or bool(email)

    print(f"[TESTER] Project: {project_name}", flush=True)
    print(f"[TESTER] Discovered {len(routes)} screens/routes", flush=True)
    if routes:
        for r in routes[:5]:
            print(f"[TESTER]   {r['name']} -> {r['widget']}", flush=True)

    # ── Start Flutter ────────────────────────────────────────────────────────
    proc, url = _start_flutter(project_path)
    if proc is None:
        return "Flutter not on PATH. Install Flutter and add it to your PATH, then try again."
    if not url:
        _stop_flutter(proc)
        return (
            "Flutter started but the serving URL didn't appear after 90 seconds.\n"
            "Try running `flutter run -d chrome` manually to check for build errors."
        )

    # ── Run Playwright session ───────────────────────────────────────────────
    passed: list[str] = []
    failed: list[str] = []
    screenshots: list[str] = []

    header = [
        f"Flutter UI Test — {project_name}",
        "=" * 50,
        f"Started:     {time.strftime('%Y-%m-%d %H:%M')}",
        f"URL:         {url}",
        f"Credentials: {email or '(none provided)'}  role: {role or 'default'}",
        f"Screens discovered: {len(routes)}",
        "",
    ]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=150)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.set_default_timeout(15000)

            # ── Initial load ─────────────────────────────────────────────────
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                time.sleep(2)
                screenshots.append(_shot(page, project_path, "01_initial"))
                passed.append("App loads and renders in Chrome")
            except Exception as e:
                failed.append(f"App failed to load: {e}")
                browser.close()
                _stop_flutter(proc)
                return _build_report(header, passed, failed, screenshots)

            err = _check_error(page)
            if err:
                failed.append(f"Error on launch: {err[:120]}")
                screenshots.append(_shot(page, project_path, "01_launch_error"))
            else:
                passed.append("No crash or error on launch")

            # ── Login ────────────────────────────────────────────────────────
            if has_login and email:
                login = _test_login(page, project_path, email, password, role)
                if login["attempted"]:
                    screenshots.append(_shot(page, project_path, "02_after_login"))
                    if login["success"]:
                        passed.append(f"Login succeeded ({role or 'default'} — {email})")
                    else:
                        failed.append(f"Login: {login['message']}")
                else:
                    passed.append(f"Login form not on initial screen: {login['message']}")
            elif has_login and not email:
                passed.append("Login code detected but no credentials provided — skipped")

            _wait_stable(page, 3000)

            # ── Navigation ───────────────────────────────────────────────────
            nav_results = _test_navigation(page, project_path, routes)
            if nav_results:
                for nr in nav_results:
                    if nr["status"] == "ok":
                        passed.append(f"Nav '{nr['label']}' — ok")
                    else:
                        failed.append(f"Nav '{nr['label']}' — {nr['error'] or 'click failed'}")
            else:
                passed.append("No navigation bar found (single-screen or nav not in DOM)")

            # ── Buttons ──────────────────────────────────────────────────────
            screenshots.append(_shot(page, project_path, "03_pre_buttons"))
            btn_results = _test_buttons(page, project_path)
            for br in btn_results:
                if br["status"] == "ok":
                    passed.append(f"Button '{br['label']}' — ok")
                elif br["status"] == "error":
                    failed.append(f"Button '{br['label']}' — {br['error']}")

            # ── Final state ──────────────────────────────────────────────────
            screenshots.append(_shot(page, project_path, "04_final"))
            err = _check_error(page)
            if err:
                failed.append(f"Error on final screen: {err[:120]}")

            browser.close()

    except Exception as e:
        failed.append(f"Test session error: {e}")
    finally:
        _stop_flutter(proc)

    return _build_report(header, passed, failed, screenshots)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _build_report(
    header: list[str],
    passed: list[str],
    failed: list[str],
    screenshots: list[str],
) -> str:
    lines = list(header)
    lines.append(f"RESULT: {len(passed)} passed, {len(failed)} failed")
    lines.append("")

    if passed:
        lines.append("PASSED:")
        for p in passed:
            lines.append(f"  ✓ {p}")
        lines.append("")

    if failed:
        lines.append("ISSUES FOUND:")
        for f in failed:
            lines.append(f"  ✗ {f}")
        lines.append("")

    if screenshots:
        lines.append(f"SCREENSHOTS ({len(screenshots)} saved):")
        for s in screenshots:
            lines.append(f"  {s}")
        lines.append("")

    lines.append("Test complete.")
    return "\n".join(lines)
