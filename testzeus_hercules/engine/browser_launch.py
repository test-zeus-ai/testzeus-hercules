"""Turn a case's ``browser`` and ``browser_options`` into Playwright's launch and context keyword arguments.

Secrets are read from the run's environment here, at launch, and go only to Playwright; they are never logged. The
auth state file is read from inside the project, and a video goes to the run's evidence folder.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from testzeus_hercules.cases.browser_options import BrowserOptions, Secret, channel_of, engine_of

__all__ = ["BrowserLaunch", "BrowserSetupError", "browser_launch"]

VIDEO_DIRNAME = "video"


class BrowserSetupError(ValueError):
    """The options cannot be used as given (a missing secret, a missing auth file). Never contains a secret."""


@dataclass(frozen=True)
class BrowserLaunch:
    """What to launch: the Playwright browser type, and the ``launch`` and ``new_context`` keyword arguments."""

    engine: str
    launch: dict[str, Any] = field(default_factory=dict, repr=False)
    context: dict[str, Any] = field(default_factory=dict, repr=False)


def _value(item: object, env: Mapping[str, str], where: str) -> Any:
    if isinstance(item, Secret):
        found = env.get(item.secret, "")
        if not found:
            raise BrowserSetupError(f"browser_options {where} needs the environment variable {item.secret}, which is not set")
        return found
    return item


def browser_launch(
    browser: str | None,
    options: BrowserOptions | None,
    *,
    env: Mapping[str, str],
    headless: bool,
    project_dir: Path | None = None,
    video_dir: Path | None = None,
    executable_path: str | None = None,
) -> BrowserLaunch:
    """The launch for ``browser`` with ``options``; ``headless`` is the run's own choice (``--headed`` makes it False)."""
    engine = engine_of(browser)
    opts = options or BrowserOptions()
    launch: dict[str, Any] = {"headless": headless and opts.headless is not False}
    channel = channel_of(browser)
    if channel:
        launch["channel"] = channel
    elif engine == "chromium" and executable_path:
        launch["executable_path"] = executable_path  # HERCULES_CHROMIUM applies to Playwright's Chromium only
    if opts.slow_mo is not None:
        launch["slow_mo"] = opts.slow_mo
    if opts.args:
        launch["args"] = list(opts.args)
    if opts.firefox_user_prefs:
        launch["firefox_user_prefs"] = dict(opts.firefox_user_prefs)
    if opts.proxy is not None:
        proxy: dict[str, Any] = {"server": opts.proxy.server}
        if opts.proxy.bypass:
            proxy["bypass"] = opts.proxy.bypass
        if opts.proxy.username is not None:
            proxy["username"] = _value(opts.proxy.username, env, "proxy.username")
        if opts.proxy.password is not None:
            proxy["password"] = _value(opts.proxy.password, env, "proxy.password")
        launch["proxy"] = proxy
    launch.update(opts.launch_extra)

    context: dict[str, Any] = {}
    for name in (
        "locale",
        "timezone_id",
        "color_scheme",
        "reduced_motion",
        "forced_colors",
        "user_agent",
        "ignore_https_errors",
        "java_script_enabled",
        "bypass_csp",
        "offline",
        "accept_downloads",
        "service_workers",
        "device_scale_factor",
        "is_mobile",
        "has_touch",
    ):
        value = getattr(opts, name)
        if value is not None:
            context[name] = value
    if opts.geolocation is not None:
        context["geolocation"] = opts.geolocation.model_dump(exclude_none=True)
    if opts.permissions:
        context["permissions"] = list(opts.permissions)
    if opts.extra_http_headers:
        context["extra_http_headers"] = {name: _value(value, env, f"extra_http_headers.{name}") for name, value in opts.extra_http_headers.items()}
    if opts.http_credentials is not None:
        credentials = {
            "username": _value(opts.http_credentials.username, env, "http_credentials.username"),
            "password": _value(opts.http_credentials.password, env, "http_credentials.password"),
        }
        if opts.http_credentials.origin:
            credentials["origin"] = opts.http_credentials.origin
        context["http_credentials"] = credentials
    if opts.storage_state is not None:
        root = (project_dir or Path.cwd()).resolve()
        state = (root / opts.storage_state).resolve()
        if not state.is_relative_to(root):
            raise BrowserSetupError("browser_options.storage_state must stay inside the project")
        if not state.is_file():
            raise BrowserSetupError(f"browser_options.storage_state: {opts.storage_state} does not exist in the project (save it with Playwright's context.storage_state(path=...))")
        context["storage_state"] = str(state)
    if opts.record_video and video_dir is not None:
        context["record_video_dir"] = str(video_dir)
    context.update(opts.context_extra)
    return BrowserLaunch(engine=engine, launch=launch, context=context)
