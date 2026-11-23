"""The environment a helper process gets: what programs need to run, never Hercules's keys and tokens.

The browser, the security scanner and an exported test's check run in processes of their own. They need the
path, the home folder, the locale, the display, proxies and CA bundles, nothing else; model keys, device cloud
credentials and the daemon's API token stay in the Hercules process.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

PASSTHROUGH = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LANGUAGE",
        "TZ",
        "TERM",
        "TMPDIR",
        "TEMP",
        "TMP",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "DBUS_SESSION_BUS_ADDRESS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "ALL_PROXY",
        "all_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "PROGRAMDATA",
        "PROGRAMFILES",
    }
)
PASSTHROUGH_PREFIXES = ("LC_",)
# A browser also needs its libraries and fonts where they are not in the usual places.
BROWSER_KEEP = ("LD_LIBRARY_PATH", "FONTCONFIG_FILE", "FONTCONFIG_PATH")


def child_environment(environ: Mapping[str, str], keep: Iterable[str] = (), prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    """From ``environ``, what a helper process needs (see the module docstring), the names in ``keep`` and those
    starting with one of ``prefixes``."""
    wanted = PASSTHROUGH | set(keep)
    starts = PASSTHROUGH_PREFIXES + prefixes
    return {name: value for name, value in environ.items() if name in wanted or name.startswith(starts)}
