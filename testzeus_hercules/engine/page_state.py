"""Save and restore where the browser was, so a resumed run continues on the same page.

After each completed step the run keeps the page's URL, cookies and local storage in
its checkpoint; a resumed run (a fresh browser) puts them back and opens the URL.
Both directions are best effort: a page without a browser context (a test fake) has
nothing to save, and a restore problem is reported, never raised.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

_MAX_ORIGINS = 20
_RESTORE_TIMEOUT_MS = 15_000
_SET_STORAGE_JS = "(items) => { for (const [key, value] of items) { try { window.localStorage.setItem(key, value); } catch (e) {} } }"


async def capture_page_state(page: Any) -> dict[str, Any] | None:
    """URL, cookies and local storage of ``page``'s context, or None when there is no real browser."""
    context = getattr(page, "context", None)
    storage_state = getattr(context, "storage_state", None)
    if not callable(storage_state):
        return None
    try:
        state = await storage_state()
    except Exception:  # a closing browser: keep the previous checkpoint's state
        return None
    return {"url": str(getattr(page, "url", "") or ""), "cookies": list(state.get("cookies", [])), "origins": list(state.get("origins", []))[:_MAX_ORIGINS]}


async def restore_page_state(page: Any, saved: Mapping[str, Any] | None) -> str | None:
    """Put cookies and local storage back and open the saved URL; returns a problem, or None."""
    if not saved:
        return None
    context = getattr(page, "context", None)
    if context is None or not hasattr(page, "goto"):
        return None
    try:
        cookies = list(saved.get("cookies") or [])
        if cookies:
            await context.add_cookies(cookies)
        for origin in saved.get("origins") or []:
            items = [(entry.get("name"), entry.get("value")) for entry in origin.get("localStorage", []) if entry.get("name") is not None]
            if not items or not str(origin.get("origin", "")).startswith(("http://", "https://")):
                continue
            await page.goto(origin["origin"], wait_until="domcontentloaded", timeout=_RESTORE_TIMEOUT_MS)
            await page.evaluate(_SET_STORAGE_JS, items)
        url = str(saved.get("url") or "")
        if url and url != "about:blank":
            with contextlib.suppress(Exception):
                await page.goto(url, wait_until="load", timeout=_RESTORE_TIMEOUT_MS)
    except Exception as exc:  # resuming on a fresh page is still better than not resuming
        return f"the page could not be fully restored ({type(exc).__name__})"
    return None


async def save_storage_state(page: Any, path: Path) -> bool:
    """Write ``page``'s cookies and local storage as a Playwright storage-state file (mode 0600), for a run that should
    start where this one is (``browser_options.storage_state``). False when there is no real browser to read."""
    saved = await capture_page_state(page)
    if saved is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"cookies": saved["cookies"], "origins": saved["origins"]}, handle)
    return True
