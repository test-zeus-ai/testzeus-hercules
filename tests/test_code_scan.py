import asyncio
import json
import subprocess
import types

import pytest
from testzeus_hercules.orchestrator.code_scan.code_summary import (
    enrich_with_llm,
    generate_code_summary,
    save_summary,
)
from testzeus_hercules.orchestrator.code_scan.git_diff import (
    get_diff_summary,
    suggest_test_targets,
    validate_base_ref,
)


def _make_fake_project(root) -> None:
    root.joinpath("package.json").write_text(
        json.dumps(
            {
                "name": "fake-app",
                "main": "server.js",
                "scripts": {"start": "node server.js"},
                "dependencies": {"react": "^18.2.0", "express": "^4.18.2"},
                "devDependencies": {"jest": "^29.0.0"},
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("main.py").write_text(
        """from fastapi import FastAPI

app = FastAPI()


@app.get("/users")
def list_users() -> dict:
    return {}


@app.post("/users")
def create_user() -> dict:
    return {}
""",
        encoding="utf-8",
    )
    root.joinpath("requirements.txt").write_text("fastapi==0.110.0\nuvicorn==0.29.0\n", encoding="utf-8")
    root.joinpath("package-lock.json").write_text(json.dumps({"lockfileVersion": 3}), encoding="utf-8")
    root.joinpath("uv.lock").write_text("", encoding="utf-8")
    root.joinpath("tests").mkdir()
    root.joinpath("tests", "test_users.py").write_text("def test_noop() -> None:\n    assert True\n", encoding="utf-8")
    # Skipped dependency dir must not contribute files.
    root.joinpath("node_modules", "leftpad").mkdir(parents=True)
    root.joinpath("node_modules", "leftpad", "index.js").write_text("module.exports = {}\n", encoding="utf-8")


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return types.SimpleNamespace(content=self.content)


class _ExplodingLLM:
    async def ainvoke(self, messages):
        raise RuntimeError("llm unavailable")


def test_generate_code_summary_on_fake_project(tmp_path) -> None:
    _make_fake_project(tmp_path)

    summary = generate_code_summary(str(tmp_path))

    assert summary["project_path"] == str(tmp_path)
    assert summary["generated_at"]

    frameworks = summary["frameworks"]
    assert any(fw.startswith("react ") for fw in frameworks)
    assert any(fw.startswith("express ") for fw in frameworks)
    assert any(fw.startswith("fastapi ") for fw in frameworks)
    assert any(fw.startswith("jest ") for fw in frameworks)

    routes = {(r["route"], r["method"], r["file"]) for r in summary["routes"]}
    assert ("/users", "GET", "main.py") in routes
    assert ("/users", "POST", "main.py") in routes

    assert "main.py" in summary["entry_points"]
    assert "server.js" in summary["entry_points"]
    assert "node server.js" in summary["entry_points"]

    assert summary["languages"][".py"] == 2  # main.py + tests/test_users.py
    assert summary["languages"][".json"] == 2  # package.json + package-lock.json
    assert ".js" not in summary["languages"]  # node_modules skipped

    assert "uv" in summary["package_managers"]
    assert "npm" in summary["package_managers"]

    assert "tests" in summary["test_dirs"]
    assert isinstance(summary["summary_text"], str) and summary["summary_text"].strip()
    assert summary["total_files"] == 6


def test_generate_code_summary_caps_and_size_limit(tmp_path) -> None:
    _make_fake_project(tmp_path)

    capped = generate_code_summary(str(tmp_path), max_files=1)
    assert capped["total_files"] == 1

    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 2048)
    limited = generate_code_summary(str(tmp_path), max_file_bytes=1024)
    assert ".bin" not in limited["languages"]


def test_generate_code_summary_empty_dir(tmp_path) -> None:
    summary = generate_code_summary(str(tmp_path))

    assert summary["total_files"] == 0
    assert summary["languages"] == {}
    assert summary["frameworks"] == []
    assert summary["routes"] == []
    assert summary["entry_points"] == []
    assert isinstance(summary["summary_text"], str) and summary["summary_text"].strip()


def test_save_summary_roundtrip(tmp_path) -> None:
    _make_fake_project(tmp_path)
    summary = generate_code_summary(str(tmp_path))

    out_path = str(tmp_path / "out" / "code_summary.json")
    assert save_summary(summary, out_path) == out_path

    with open(out_path, encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["frameworks"] == summary["frameworks"]
    assert loaded["routes"] == summary["routes"]


def test_enrich_with_llm_success() -> None:
    summary = {"summary_text": "Fake project.", "frameworks": ["react (package.json)"], "routes": [{"route": "/users", "method": "GET", "file": "main.py"}]}
    llm = _FakeLLM('```json\n{"notes": "CRUD app", "testable_areas": ["user listing"]}\n```')

    enriched = asyncio.run(enrich_with_llm(summary, llm=llm))

    assert enriched["llm_notes"] == "CRUD app"
    assert enriched["testable_areas"] == ["user listing"]
    assert "llm_notes_error" not in enriched
    assert "/users" in llm.calls[0][0].content  # routes preview included in prompt


def test_enrich_with_llm_failure_is_defensive() -> None:
    summary = {"summary_text": "Fake project.", "frameworks": [], "routes": []}

    enriched = asyncio.run(enrich_with_llm(summary, llm=_ExplodingLLM()))

    assert "llm_notes" not in enriched
    assert "testable_areas" not in enriched
    assert "llm_notes_error" in enriched

    # Malformed JSON from a well-behaved transport is also handled.
    bad_json_llm = _FakeLLM("not json at all")
    enriched_bad = asyncio.run(enrich_with_llm(dict(summary), llm=bad_json_llm))
    assert "llm_notes" not in enriched_bad
    assert "llm_notes_error" in enriched_bad


def _git(repo, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo_with_two_commits(repo) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tester@example.com")
    _git(repo, "config", "user.name", "Tester")
    repo.joinpath("a.txt").write_text("first\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first commit")
    repo.joinpath("src").mkdir()
    repo.joinpath("src", "b.py").write_text("print('second')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second commit")


def test_get_diff_summary_between_commits(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo_with_two_commits(repo)

    diff = get_diff_summary(str(repo), base_ref="HEAD~1")

    changed = {entry["path"]: entry["status"] for entry in diff["changed_files"]}
    assert changed.get("src/b.py") == "A"
    assert diff["counts"]["added"] == 1
    assert diff["counts"]["untracked"] == 0
    assert diff["warnings"] == []
    assert "src/b.py" in diff["diff_stat"]


def test_get_diff_summary_uncommitted_and_untracked(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo_with_two_commits(repo)
    repo.joinpath("a.txt").write_text("modified\n", encoding="utf-8")
    repo.joinpath("new_thing.py").write_text("x = 1\n", encoding="utf-8")

    diff = get_diff_summary(str(repo), base_ref="HEAD~1")

    assert diff["counts"]["modified"] == 1
    assert diff["counts"]["untracked"] == 1
    statuses = {(entry["path"], entry["status"]) for entry in diff["changed_files"]}
    assert ("a.txt", "M") in statuses
    assert ("new_thing.py", "A") in statuses


def test_get_diff_summary_bad_base_ref_falls_back(tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo_with_two_commits(repo)
    repo.joinpath("pending.py").write_text("x = 2\n", encoding="utf-8")

    diff = get_diff_summary(str(repo), base_ref="does-not-exist")

    assert any("base_ref" in warning for warning in diff["warnings"])
    assert ("pending.py", "A") in {(entry["path"], entry["status"]) for entry in diff["changed_files"]}


@pytest.mark.parametrize("bad_ref", ["--output=pwned.txt", "-p", "HEAD~1 --stat", "a;b", "", "--help"])
def test_get_diff_summary_rejects_option_like_base_ref(tmp_path, bad_ref) -> None:
    repo = tmp_path / "repo"
    _init_repo_with_two_commits(repo)

    with pytest.raises(ValueError, match="Invalid base_ref"):
        get_diff_summary(str(repo), base_ref=bad_ref)

    assert not (repo / "pwned.txt").exists()


@pytest.mark.parametrize("good_ref", ["HEAD~1", "origin/main", "v1.2.3", "feature/x-y", "HEAD@{1}", "abc123"])
def test_validate_base_ref_accepts_normal_refs(good_ref) -> None:
    assert validate_base_ref(good_ref) == good_ref


def test_get_diff_summary_non_git_dir_raises(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ValueError, match="Not a git repository"):
        get_diff_summary(str(plain))


def test_suggest_test_targets_area_mapping() -> None:
    diff_summary = {
        "changed_files": [
            {"path": "auth/login.py", "status": "M"},
            {"path": "src/billing/invoice.ts", "status": "A"},
            {"path": "README.md", "status": "M"},
        ]
    }

    assert suggest_test_targets(diff_summary) == ["auth", "billing", "readme"]


def test_suggest_test_targets_with_plan_cases() -> None:
    diff_summary = {
        "changed_files": [
            {"path": "auth/login.py", "status": "M"},
            {"path": "payments/checkout.js", "status": "A"},
        ]
    }
    plan_cases = [
        {"id": "tc-1", "tags": ["auth"], "title": "Login flow works"},
        {"id": "tc-2", "tags": ["billing"], "description": "Invoice generation"},
        {"id": "tc-3", "tags": ["unrelated"], "title": "Something else"},
        {"id": "tc-4", "tags": [], "title": "checkout page renders"},
    ]

    assert suggest_test_targets(diff_summary, plan_cases) == ["tc-1", "tc-4"]


@pytest.mark.parametrize(
    "line,expected",
    [
        (" M src/app.py", {"path": "src/app.py", "status": "M"}),
        ("M  src/app.py", {"path": "src/app.py", "status": "M"}),
        ("MM src/app.py", {"path": "src/app.py", "status": "M"}),
        ("A  new file.py", {"path": "new file.py", "status": "A"}),
        (" D gone.py", {"path": "gone.py", "status": "D"}),
        ("R  old.py -> renamed.py", {"path": "renamed.py", "status": "R"}),
    ],
)
def test_parse_porcelain_fixed_width_columns(line, expected) -> None:
    from testzeus_hercules.orchestrator.code_scan.git_diff import _parse_porcelain

    entries, untracked = _parse_porcelain(line + "\n")
    assert entries == [expected]
    assert untracked == 0


def test_parse_porcelain_untracked_counted() -> None:
    from testzeus_hercules.orchestrator.code_scan.git_diff import _parse_porcelain

    entries, untracked = _parse_porcelain("?? notes.txt\n M a.py\n")
    assert untracked == 1
    assert entries == [{"path": "notes.txt", "status": "A"}, {"path": "a.py", "status": "M"}]
