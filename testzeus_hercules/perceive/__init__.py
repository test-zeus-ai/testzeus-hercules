"""Set-of-Mark perception: numbered marks on a screenshot plus a matching element list.

``take_snapshot`` walks every frame and open shadow root, gives each interactive element an ``md`` id,
paints the on-screen ones onto a plain screenshot (off-page) and returns an immutable ``Snapshot``.
``resolve`` turns an ``md`` back into a Playwright ``Locator``. A page that cannot be read at all
raises ``PerceptionError`` (never an empty snapshot); what could not be read on a page that could is
listed in the snapshot's ``notes``.
"""

from testzeus_hercules.perceive.model import BBox, Element, ElementPath, Snapshot
from testzeus_hercules.perceive.snapshot import ElementGone, PerceptionError, resolve, take_snapshot

__all__ = ["BBox", "Element", "ElementGone", "ElementPath", "PerceptionError", "Snapshot", "resolve", "take_snapshot"]
