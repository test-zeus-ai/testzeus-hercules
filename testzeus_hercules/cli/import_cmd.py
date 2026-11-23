"""``hercules import v1|openapi|doc``: bring existing material into ``.hercules/`` as test cases.

Exit codes: 0 when the import ran (the report lists anything skipped), 1 when it
ran but converted no case, 2 when the source cannot be imported at all.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from testzeus_hercules.cases.importers.common import ImportReport


def add_import_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = commands.add_parser("import", help="import v1 projects, OpenAPI specs or requirement documents as test cases", description="Import existing material into .hercules/ as test cases.")
    kinds = parser.add_subparsers(dest="import_kind", metavar="<source>", required=True)

    v1 = kinds.add_parser("v1", help="a v1 project folder (opt/input, opt/test_data, .env)", description="Convert a v1 project's feature files, test data and .env.")
    v1.add_argument("source", type=Path, metavar="DIR", help="the v1 project folder (the one holding opt/, or opt/ itself)")
    v1.add_argument("--base-url", default=None, metavar="URL", help="base URL for the environment (default: BASE_URL in .env, else the first URL in the features)")
    v1.add_argument("--env-name", default="default", metavar="NAME", help="name of the environment to write (default: default)")
    v1.add_argument("--env-file", type=Path, default=None, metavar="FILE", help="read variables from this file instead of .env")

    openapi = kinds.add_parser("openapi", help="an OpenAPI 3.x spec: one API case per operation", description="Generate one API test case per operation of an OpenAPI 3.x spec.")
    openapi.add_argument("source", type=Path, metavar="SPEC", help="the spec file (.yaml, .yml or .json)")
    openapi.add_argument("--base-url", default=None, metavar="URL", help="where the API is served (default: the spec's first server)")
    openapi.add_argument("--tag", dest="tags", action="append", default=[], metavar="TAG", help="only operations with this tag (repeatable)")

    doc = kinds.add_parser("doc", help="a requirements document (md, txt, pdf, docx) as draft cases", description="Extract a requirements document and write draft test cases.")
    doc.add_argument("source", type=Path, metavar="FILE", help="the document")
    doc.add_argument("--draft", action="store_true", help="ask the local model to propose draft cases (falls back to a TODO template)")

    for sub in (v1, openapi, doc):
        sub.add_argument("--project", type=Path, default=None, metavar="DIR", help="project directory (default: the current directory)")
        sub.add_argument("--dry-run", action="store_true", help="show what would be written and write nothing")
        sub.add_argument("--force", action="store_true", help="replace files that already exist")


def run_import(args: argparse.Namespace, *, env: Mapping[str, str] | None, out: TextIO, err: TextIO) -> int:
    from testzeus_hercules.cases.importers.common import ImporterError

    project = (args.project or Path.cwd()).expanduser().resolve()
    try:
        report = _run(args, project, env)
    except ImporterError as exc:
        err.write(f"hercules import {args.import_kind}: {exc}\n")
        return 2
    out.write(report.render())
    return 0 if report.cases_converted or not report.skipped else 1


def _run(args: argparse.Namespace, project: Path, env: Mapping[str, str] | None) -> ImportReport:
    if args.import_kind == "v1":
        from testzeus_hercules.cases.importers.v1 import V1ImportOptions, import_v1

        return import_v1(V1ImportOptions(source_dir=args.source, project_dir=project, dry_run=args.dry_run, force=args.force, base_url=args.base_url, env_name=args.env_name, env_file=args.env_file))
    if args.import_kind == "openapi":
        from testzeus_hercules.cases.importers.openapi import OpenAPIImportOptions, import_openapi

        return import_openapi(OpenAPIImportOptions(spec_path=args.source, project_dir=project, base_url=args.base_url, tags=tuple(args.tags), dry_run=args.dry_run, force=args.force))
    from testzeus_hercules.cases.importers.doc import DocImportOptions, import_doc

    return import_doc(DocImportOptions(doc_path=args.source, project_dir=project, draft=args.draft, dry_run=args.dry_run, force=args.force), env=env)
