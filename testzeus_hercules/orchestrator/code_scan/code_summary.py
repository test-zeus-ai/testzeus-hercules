"""Heuristic, offline codebase summary generation.

Walks a project tree and produces a structured summary (languages,
frameworks, entry points, routes, package managers, test directories)
without requiring an LLM. An optional LLM enrichment hook
(`enrich_with_llm`) can add human-readable notes and testable areas.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from testzeus_hercules.utils.logger import logger

SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", "vendor", "target", "opt", ".hercules"})

MAX_ROUTES = 200
ROUTES_PREVIEW_LIMIT = 30

NODE_FRAMEWORK_DEPS: dict[str, str] = {
    "react": "react",
    "next": "next",
    "vue": "vue",
    "nuxt": "nuxt",
    "svelte": "svelte",
    "@angular/core": "angular",
    "express": "express",
    "fastify": "fastify",
    "@nestjs/core": "nestjs",
    "vite": "vite",
    "jest": "jest",
    "playwright": "playwright",
    "@playwright/test": "playwright",
    "cypress": "cypress",
}

PYTHON_FRAMEWORKS = frozenset({"django", "flask", "fastapi", "sqlalchemy", "pytest", "playwright"})

PACKAGE_MANAGERS = {
    "package-lock.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "uv.lock": "uv",
    "poetry.lock": "poetry",
    "Pipfile": "pipenv",
    "Cargo.lock": "cargo",
}

TEST_DIR_NAMES = ("tests", "test", "__tests__", "spec", "e2e")

ENTRY_POINT_CANDIDATES = (
    "main.py",
    "app.py",
    "manage.py",
    "server.py",
    "wsgi.py",
    "asgi.py",
    "index.ts",
    "index.js",
    "src/index.ts",
    "src/main.ts",
    "src/main.js",
)

_CODE_SUFFIXES = frozenset({".py", ".js", ".jsx", ".ts", ".tsx"})
_PAGE_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx"})

_PY_ROUTE_RE = re.compile(r"@(\w+)\.(get|post|put|delete|patch|route|api_route)\(\s*[\"']([^\"']+)")
_PY_METHODS_RE = re.compile(r"methods\s*=\s*\[([^\]]*)\]")
_JS_ROUTE_RE = re.compile(r"\b(?:app|router)\.(get|post|put|delete|patch|all|use)\(\s*[\"'`]([^\"'`]+)")


def _collect_files(project_path: str, max_files: int, max_file_bytes: int) -> list[tuple[str, str]]:
    """Return (posix_relpath, abs_path) pairs, skipping junk dirs and oversized files."""
    root = Path(project_path)
    collected: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        dirnames.sort()
        rel_dir = PurePosixPath(Path(dirpath).relative_to(root).as_posix())
        for name in sorted(filenames):
            abs_path = os.path.join(dirpath, name)
            rel = (rel_dir / name).as_posix() if str(rel_dir) != "." else name
            try:
                if os.path.getsize(abs_path) > max_file_bytes:
                    continue
            except OSError:
                continue
            collected.append((rel, abs_path))
            if len(collected) >= max_files:
                return collected
    return collected


def _read_text(abs_path: str) -> str:
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _read_json(abs_path: str) -> dict[str, Any]:
    try:
        data = json.loads(_read_text(abs_path))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _manifest_evidence(framework: str, manifest_rel: str) -> str:
    return f"{framework} ({manifest_rel})"


def _detect_frameworks(files: list[tuple[str, str]]) -> list[str]:
    """Detect frameworks from manifest files, recording evidence per framework."""
    evidence: set[str] = set()
    for rel, abs_path in files:
        name = PurePosixPath(rel).name
        if name == "package.json":
            data = _read_json(abs_path)
            deps: dict[str, Any] = {}
            deps.update(data.get("dependencies") or {})
            deps.update(data.get("devDependencies") or {})
            for dep, framework in NODE_FRAMEWORK_DEPS.items():
                if dep in deps:
                    evidence.add(_manifest_evidence(framework, rel))
        elif name == "requirements.txt":
            for line in _read_text(abs_path).splitlines():
                req = re.split(r"[<>=~!;\[\s]", line.strip(), maxsplit=1)[0].lower()
                if req in PYTHON_FRAMEWORKS:
                    evidence.add(_manifest_evidence(req, rel))
        elif name == "pyproject.toml":
            content = _read_text(abs_path)
            for framework in PYTHON_FRAMEWORKS:
                if re.search(rf"(?i)\b{re.escape(framework)}\b", content):
                    evidence.add(_manifest_evidence(framework, rel))
        elif name == "go.mod":
            evidence.add(_manifest_evidence("go", rel))
        elif name == "Gemfile":
            evidence.add(_manifest_evidence("rails", rel))
        elif name in ("pom.xml", "build.gradle"):
            content = _read_text(abs_path).lower()
            if "spring" in content:
                evidence.add(_manifest_evidence("java-spring", rel))
            evidence.add(_manifest_evidence("java", rel))
        elif name == "composer.json":
            data = _read_json(abs_path)
            deps = {}
            deps.update(data.get("require") or {})
            deps.update(data.get("require-dev") or {})
            if "laravel/framework" in deps:
                evidence.add(_manifest_evidence("laravel", rel))
            if any(str(dep).startswith("symfony/") for dep in deps):
                evidence.add(_manifest_evidence("symfony", rel))
    return sorted(evidence)


def _detect_entry_points(files: list[tuple[str, str]]) -> list[str]:
    rel_files = {rel for rel, _ in files}
    entry_points: list[str] = []
    for candidate in ENTRY_POINT_CANDIDATES:
        if candidate in rel_files and candidate not in entry_points:
            entry_points.append(candidate)
    for rel, abs_path in files:
        if PurePosixPath(rel).name != "package.json":
            continue
        data = _read_json(abs_path)
        main_field = data.get("main")
        if isinstance(main_field, str) and main_field and main_field not in entry_points:
            entry_points.append(main_field)
        scripts = data.get("scripts")
        start_cmd = scripts.get("start") if isinstance(scripts, dict) else None
        if isinstance(start_cmd, str) and start_cmd and start_cmd not in entry_points:
            entry_points.append(start_cmd)
    return entry_points


def _python_route_from_line(line: str) -> tuple[str, str | None] | None:
    match = _PY_ROUTE_RE.search(line)
    if not match:
        return None
    var, verb, route = match.groups()
    if var.lower() not in {"app", "router", "api", "blueprint", "bp"}:
        return None
    if verb in {"get", "post", "put", "delete", "patch"}:
        return route, verb.upper()
    methods_match = _PY_METHODS_RE.search(line, match.end())
    if methods_match:
        methods = re.findall(r"[\"'](\w+)[\"']", methods_match.group(1))
        if methods:
            return route, ", ".join(m.upper() for m in methods)
    return route, None


def _js_route_from_line(line: str) -> tuple[str, str | None] | None:
    match = _JS_ROUTE_RE.search(line)
    if not match:
        return None
    verb, route = match.groups()
    if verb == "use":
        return route, None
    return route, verb.upper()


def _nextjs_page_route(rel: str) -> str | None:
    """Derive a route from a Next.js pages/ file or app/**/page.tsx file path."""
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    if parts[0] == "pages" and len(parts) > 1:
        if name.startswith("_") or PurePosixPath(name).suffix.lower() not in _PAGE_SUFFIXES:
            return None
        segments = [p for p in parts[1:-1]]
        if PurePosixPath(name).stem != "index":
            segments.append(PurePosixPath(name).stem)
        return "/" + "/".join(segments) if segments else "/"
    if parts[0] == "app" and len(parts) > 2 and name in {"page.tsx", "page.js", "page.jsx"}:
        segments = [p for p in parts[1:-1]]
        return "/" + "/".join(segments) if segments else "/"
    return None


def _detect_routes(files: list[tuple[str, str]]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str]] = set()

    def add(route: str, method: str | None, file: str) -> None:
        key = (route, method, file)
        if key not in seen and len(routes) < MAX_ROUTES:
            seen.add(key)
            routes.append({"route": route, "method": method, "file": file})

    for rel, abs_path in files:
        suffix = PurePosixPath(rel).suffix.lower()
        if suffix in _CODE_SUFFIXES:
            for line in _read_text(abs_path).splitlines():
                if suffix == ".py":
                    found = _python_route_from_line(line)
                    if found:
                        add(found[0], found[1], rel)
                else:
                    found = _js_route_from_line(line)
                    if found:
                        add(found[0], found[1], rel)
        page_route = _nextjs_page_route(rel)
        if page_route:
            add(page_route, None, rel)
    return routes


def _detect_package_managers(files: list[tuple[str, str]]) -> list[str]:
    found = set()
    for rel, _ in files:
        manager = PACKAGE_MANAGERS.get(PurePosixPath(rel).name)
        if manager:
            found.add(manager)
    return sorted(found)


def _detect_test_dirs(project_path: str) -> list[str]:
    return [d for d in TEST_DIR_NAMES if os.path.isdir(os.path.join(project_path, d))]


def _build_summary_text(
    project_path: str,
    languages: dict[str, int],
    total_files: int,
    frameworks: list[str],
    entry_points: list[str],
    routes: list[dict[str, Any]],
    package_managers: list[str],
) -> str:
    top_langs = ", ".join(f"{ext} ({count})" for ext, count in Counter(languages).most_common(3))
    parts = [f"Project at {project_path} contains {total_files} files."]
    if top_langs:
        parts.append(f"Top languages: {top_langs}.")
    if frameworks:
        parts.append(f"Frameworks detected: {', '.join(frameworks)}.")
    entry_preview = ", ".join(entry_points[:5]) or "none"
    parts.append(f"Entry points found: {len(entry_points)} ({entry_preview}).")
    parts.append(f"HTTP routes detected: {len(routes)}.")
    if package_managers:
        parts.append(f"Package managers: {', '.join(package_managers)}.")
    return " ".join(parts)


def generate_code_summary(project_path: str, max_files: int = 5000, max_file_bytes: int = 512_000) -> dict:
    """Walk a project tree and return a heuristic, LLM-free summary dict."""
    files = _collect_files(project_path, max_files=max_files, max_file_bytes=max_file_bytes)
    languages: dict[str, int] = dict(sorted(Counter(PurePosixPath(rel).suffix.lower() for rel, _ in files if PurePosixPath(rel).suffix).items()))
    frameworks = _detect_frameworks(files)
    entry_points = _detect_entry_points(files)
    routes = _detect_routes(files)
    package_managers = _detect_package_managers(files)
    test_dirs = _detect_test_dirs(project_path)
    total_files = len(files)
    return {
        "project_path": project_path,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "languages": languages,
        "total_files": total_files,
        "frameworks": frameworks,
        "entry_points": entry_points,
        "routes": routes,
        "package_managers": package_managers,
        "test_dirs": test_dirs,
        "summary_text": _build_summary_text(project_path, languages, total_files, frameworks, entry_points, routes, package_managers),
    }


def _extract_json_object(content: str) -> dict[str, Any]:
    """Defensively extract a JSON object from an LLM response string."""
    cleaned = content.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("LLM response does not contain a JSON object") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON is not an object")
    return parsed


async def enrich_with_llm(summary: dict, llm: Any | None = None) -> dict:
    """Optionally enrich a code summary with LLM notes and testable areas.

    On any failure the summary is returned unchanged except for a
    ``llm_notes_error`` key describing the failure.
    """
    try:
        if llm is None:
            from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model

            llm = get_litellm_chat_model("planner_agent")
        route_lines = [f"{route['method'] or 'ANY'} {route['route']} ({route['file']})" for route in (summary.get("routes") or [])[:ROUTES_PREVIEW_LIMIT]]
        prompt = (
            "You are a senior QA architect. Given the codebase summary below, respond with JSON only, "
            'using the keys "notes" (a short string of observations relevant for end-to-end testing) and '
            '"testable_areas" (a list of short strings naming testable feature areas).\n\n'
            f"Summary:\n{summary.get('summary_text', '')}\n\n"
            f"Frameworks: {', '.join(summary.get('frameworks') or [])}\n\n"
            f"Routes:\n" + ("\n".join(route_lines) if route_lines else "(none detected)")
        )
        from langchain_core.messages import HumanMessage

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        parsed = _extract_json_object(str(content))
        notes = parsed.get("notes")
        if not isinstance(notes, str):
            raise ValueError("LLM response missing string 'notes'")
        areas = parsed.get("testable_areas")
        if isinstance(areas, str):
            areas = [areas]
        if not isinstance(areas, list):
            areas = []
        summary["llm_notes"] = notes
        summary["testable_areas"] = [str(area) for area in areas]
    except Exception as exc:
        logger.warning("Code summary LLM enrichment failed: %s", exc)
        summary["llm_notes_error"] = str(exc)
    return summary


def save_summary(summary: dict, out_path: str) -> str:
    """Persist a summary dict as JSON and return the output path."""
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return out_path
