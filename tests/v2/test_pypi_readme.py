"""scripts/pypi_readme.py: the README's relative links become absolute for the PyPI package page."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pypi_readme", ROOT / "scripts" / "pypi_readme.py")
assert _spec is not None and _spec.loader is not None
pypi_readme = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pypi_readme)

REPO, REF = "test-zeus-ai/testzeus-hercules", "v2.0.0"


def test_pages_point_at_the_tag_and_pictures_at_raw_files() -> None:
    text = '[Guide](docs/guide/index.md#start) ![logo](./docs/assets/logo.png "Logo")\n<a href="docs/x.md"><img src="docs/assets/v.jpg" width="400"></a>\n<source srcset="docs/assets/dark.svg">'
    out = pypi_readme.rewrite(text, REPO, REF)
    assert "(https://github.com/test-zeus-ai/testzeus-hercules/blob/v2.0.0/docs/guide/index.md#start)" in out
    assert '(https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/v2.0.0/docs/assets/logo.png "Logo")' in out
    assert 'href="https://github.com/test-zeus-ai/testzeus-hercules/blob/v2.0.0/docs/x.md"' in out
    assert 'src="https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/v2.0.0/docs/assets/v.jpg"' in out
    assert 'srcset="https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/v2.0.0/docs/assets/dark.svg"' in out


def test_absolute_links_anchors_and_mail_are_kept() -> None:
    text = '[a](https://testzeus.com) [b](#models) [c](mailto:x@example.test) <a href="https://github.com/user-attachments/assets/1">v</a>'
    assert pypi_readme.rewrite(text, REPO, REF) == text


def test_the_real_readme_has_no_relative_link_left(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text((ROOT / "README.md").read_text(encoding="utf-8"), encoding="utf-8")
    assert pypi_readme.main([str(readme), "--ref", REF]) == 0
    text = readme.read_text(encoding="utf-8")
    relative = [t for t in re.findall(r'(?:\]\(|(?:src|srcset|href)=")([^)"\s]+)', text) if not re.match(r"^(https?:|#|mailto:)", t)]
    assert relative == []
