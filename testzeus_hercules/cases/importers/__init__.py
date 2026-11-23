"""Importers that turn existing material into v2 test cases under ``.hercules/``.

* :mod:`~testzeus_hercules.cases.importers.v1` - a v1 project folder (``opt/input``
  feature files, ``opt/test_data`` and ``.env``);
* :mod:`~testzeus_hercules.cases.importers.openapi` - an OpenAPI 3.x spec, one API case per operation;
* :mod:`~testzeus_hercules.cases.importers.doc` - a requirements document (md, txt, pdf, docx), as draft cases.

Every importer returns an :class:`~testzeus_hercules.cases.importers.common.ImportReport`
and never replaces an existing file unless asked to. Submodules are imported on use.
"""

from testzeus_hercules.cases.importers.common import Converted, ImporterError, ImportReport, Skipped

__all__ = ["Converted", "ImportReport", "ImporterError", "Skipped"]
