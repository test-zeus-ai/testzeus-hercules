"""Make the README's relative links absolute, for the package page on PyPI.

PyPI shows README.md as the project description, but it has no repository to resolve relative paths against, so
``docs/guide/index.md`` and ``docs/assets/...jpg`` would be broken links and missing pictures there. The release
workflow runs this on the checked-out tag just before building:

    python scripts/pypi_readme.py --ref v2.0.0 [--repo test-zeus-ai/testzeus-hercules] [README.md]

Pictures point at raw.githubusercontent.com and pages at github.com/.../blob/, both pinned to the release's tag, so
the PyPI page shows the README exactly as it was at that release. Absolute URLs, anchors (#...) and mailto: links
are left as they are. The file is rewritten in place; only the release build sees the change.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = "test-zeus-ai/testzeus-hercules"
_PICTURE = re.compile(r"\.(png|jpe?g|gif|svg|webp)$", re.IGNORECASE)
# Every Markdown link target, including the outer one of a linked picture such as a badge: [![..](img)](page).
_MARKDOWN = re.compile(r"(\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
_HTML = re.compile(r"""(\b(?:src|srcset|href)=)(["'])([^"']+)\2""")


def _absolute(target: str, repo: str, ref: str) -> str:
    if not target or target.startswith(("#", "/", "mailto:")) or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
        return target
    path, hash_mark, anchor = target.partition("#")
    path = path.removeprefix("./")
    if _PICTURE.search(path):
        return f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    return f"https://github.com/{repo}/blob/{ref}/{path}{hash_mark}{anchor}"


def rewrite(text: str, repo: str, ref: str) -> str:
    """``text`` with every relative Markdown and HTML link or picture made absolute at ``ref``."""
    text = _MARKDOWN.sub(lambda m: m.group(1) + _absolute(m.group(2), repo, ref) + m.group(3), text)
    return _HTML.sub(lambda m: f"{m.group(1)}{m.group(2)}{_absolute(m.group(3), repo, ref)}{m.group(2)}", text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("readme", nargs="?", default="README.md", type=Path)
    parser.add_argument("--ref", required=True, help="the release tag (or commit) the links point at")
    parser.add_argument("--repo", default=REPO, help=f"owner/name on GitHub (default {REPO})")
    args = parser.parse_args(argv)
    original = args.readme.read_text(encoding="utf-8")
    args.readme.write_text(rewrite(original, args.repo, args.ref), encoding="utf-8")
    left = [target for target in re.findall(r"\]\(([^)\s#]+)", rewrite(original, args.repo, args.ref)) if "://" not in target and not target.startswith("mailto:")]
    if left:
        print(f"relative links left in {args.readme}: {', '.join(left)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
