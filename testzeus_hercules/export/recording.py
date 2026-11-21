"""A recording of an exported test's run: its Playwright trace's screen frames as an animated GIF.

Playwright records video only with its ffmpeg helper; a trace needs nothing extra and holds a frame
for every change on the screen. The frames show the page only (no address bar), so the recording can
be shared where the trace (which holds what the browser sent, secrets included) must not be.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

# The recording's frames at most this wide or high, each shown at least / at most this long.
MAX_SIDE = 960
MIN_FRAME_MS = 60
MAX_FRAME_MS = 1500
_COLORS = 128


def trace_frames(trace: Path) -> list[tuple[float, bytes]]:
    """The trace's screen frames in order: (timestamp in ms, JPEG bytes)."""
    with zipfile.ZipFile(trace) as archive:
        names = set(archive.namelist())
        frames: list[tuple[float, str]] = []
        for name in names:
            if not name.endswith(".trace"):
                continue
            for line in archive.read(name).decode("utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "screencast-frame" and event.get("sha1"):
                    frames.append((float(event.get("timestamp") or 0), f"resources/{event['sha1']}"))
        return [(stamp, archive.read(resource)) for stamp, resource in sorted(frames) if resource in names]


def write_recording(trace: Path, out: Path) -> Path | None:
    """Write the trace's frames to ``out`` as an animated GIF, each shown for as long as it was on screen; None when
    the trace has no frames."""
    from PIL import Image

    frames = trace_frames(trace)
    if not frames:
        return None
    images = []
    for _, data in frames:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        images.append(image.quantize(colors=_COLORS, method=Image.Quantize.MEDIANCUT))
    stamps = [stamp for stamp, _ in frames]
    durations = [int(min(max(later - stamp, MIN_FRAME_MS), MAX_FRAME_MS)) for stamp, later in zip(stamps, [*stamps[1:], stamps[-1] + MAX_FRAME_MS])]
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(out, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True)
    return out
