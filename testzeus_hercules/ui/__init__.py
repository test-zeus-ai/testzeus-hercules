"""The local tester UI: built static assets (``static/``) and the rules for serving them.

The React source lives in the repository's top-level ``ui/`` directory; ``npm run build``
(or ``scripts/build_ui.sh``) writes the bundle to ``testzeus_hercules/ui/static/``, which
ships inside the wheel. Nothing here imports the daemon.
"""

from testzeus_hercules.ui.assets import STATIC_DIR, UI_CSP, StaticAsset, index_page, missing_assets_page, resolve_asset, sign_in_page

__all__ = ["STATIC_DIR", "UI_CSP", "StaticAsset", "index_page", "missing_assets_page", "resolve_asset", "sign_in_page"]
