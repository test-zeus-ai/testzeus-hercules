"""Security scans of the app under test with the nuclei scanner (an optional external tool).

* :mod:`~testzeus_hercules.scan.install`: find the scanner, or install a pinned release on request.
* :mod:`~testzeus_hercules.scan.scope`: what a scan may target (the base URL, or a spec in the project).
* :mod:`~testzeus_hercules.scan.nuclei`: the command, the process and its findings.
* :mod:`~testzeus_hercules.scan.redact`: secrets masked in the command, the output and the findings.
* :mod:`~testzeus_hercules.scan.runner`: one scan from request to result and evidence.

Used by security steps (``Step.security``, run by the engine without a browser) and by
``hercules scan``.
"""
