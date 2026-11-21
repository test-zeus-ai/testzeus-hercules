// The sidebar's page change: the outgoing page is frozen on top as a static copy and fades out in square blocks,
// in a wave from the click, so the incoming page shows through. Nothing is drawn over it: the in-between blocks are
// the two pages mixed. Skipped when the viewer asks for reduced motion.

export const TILE_PX = 48;
export const DURATION_MS = 280;
/** Share of the duration each block takes to fade; the rest spreads the blocks' starts across the wave. */
const FADE_SHARE = 0.32;
/** Share of a block's start that is random, for a ragged front. */
const JITTER = 0.2;

export interface Cell {
  x: number;
  y: number;
  start: number;
}

export interface Point {
  x: number;
  y: number;
}

/** The blocks covering a `width` × `height` area, each with when (ms) it starts to fade: the farther from `origin`, the later. */
export function dissolveCells(width: number, height: number, origin: Point, { tile = TILE_PX, duration = DURATION_MS, random = Math.random } = {}): Cell[] {
  const cols = Math.max(1, Math.ceil(width / tile));
  const rows = Math.max(1, Math.ceil(height / tile));
  const x = Math.min(Math.max(origin.x, 0), width);
  const y = Math.min(Math.max(origin.y, 0), height);
  const far = Math.max(Math.hypot(x, y), Math.hypot(width - x, y), Math.hypot(x, height - y), Math.hypot(width - x, height - y)) || 1;
  const sweep = duration * (1 - FADE_SHARE);
  const cells: Cell[] = [];
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const distance = Math.hypot((col + 0.5) * tile - x, (row + 0.5) * tile - y) / far;
      cells.push({ x: col * tile, y: row * tile, start: Math.min(sweep, (distance * (1 - JITTER) + random() * JITTER) * sweep) });
    }
  }
  return cells;
}

/** How much of a block still shows at `elapsed` ms: 1 before it starts, 0 once it has faded. */
export function cellAlpha(cell: Cell, elapsed: number, duration = DURATION_MS): number {
  const alpha = 1 - (elapsed - cell.start) / (duration * FADE_SHARE);
  return Math.min(1, Math.max(0, alpha));
}

/** Opacity levels a block passes through; plenty for a smooth fade, and equal neighbours merge into one layer. */
const LEVELS = 24;

/**
 * The CSS `mask` for one frame: one gradient layer per run of neighbouring blocks in a row at the same opacity
 * (gradients, not images: the daemon's Content Security Policy allows no data: URLs). A frame with nothing left
 * showing masks everything out.
 */
export function maskFrame(cells: Cell[], elapsed: number, width: number, { tile = TILE_PX, duration = DURATION_MS } = {}): string {
  const cols = Math.max(1, Math.ceil(width / tile));
  const layers: string[] = [];
  let run: { x: number; y: number; w: number; level: number } | null = null;
  const flush = () => {
    if (!run) return;
    const alpha = (run.level / LEVELS).toFixed(3);
    layers.push(`linear-gradient(rgba(0,0,0,${alpha}),rgba(0,0,0,${alpha})) ${run.x}px ${run.y}px / ${run.w}px ${tile}px no-repeat`);
    run = null;
  };
  cells.forEach((cell, index) => {
    const level = Math.round(cellAlpha(cell, elapsed, duration) * LEVELS);
    const rowStart = index % cols === 0;
    if (rowStart || !run || run.level !== level) {
      flush();
      if (level > 0) run = { x: cell.x, y: cell.y, w: tile, level };
    } else {
      run.w += tile;
    }
  });
  flush();
  return layers.length ? layers.join(", ") : "linear-gradient(transparent, transparent)";
}

export function prefersReducedMotion(): boolean {
  try {
    return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

let cancelRunning: (() => void) | null = null;

/** A static copy of `page` without ids (so labels and lookups keep finding the live page), with canvases painted in. */
function frozenCopy(page: HTMLElement): HTMLElement {
  const copy = page.cloneNode(true) as HTMLElement;
  copy.removeAttribute("id");
  copy.querySelectorAll("[id]").forEach((element) => element.removeAttribute("id"));
  const sources = page.querySelectorAll("canvas");
  copy.querySelectorAll("canvas").forEach((canvas, index) => {
    try {
      canvas.getContext("2d")?.drawImage(sources[index], 0, 0);
    } catch {
      // a tainted or 3D canvas stays blank in the copy; it is gone in a moment
    }
  });
  return copy;
}

/**
 * Freeze `page` (the main content area) and dissolve it from `origin` (viewport coordinates of the click; null:
 * the area's left edge, half way down). Call it before the route changes. A second call ends the first at once.
 */
export function dissolvePage(page: HTMLElement | null, origin: Point | null): void {
  cancelRunning?.();
  if (!page || prefersReducedMotion() || typeof requestAnimationFrame !== "function") return;
  const area = page.getBoundingClientRect();
  const top = Math.max(area.top, 0);
  const bottom = Math.min(area.bottom, window.innerHeight);
  const width = Math.round(area.width);
  const height = Math.round(bottom - top);
  if (width <= 0 || height <= 0) return;

  const layer = document.createElement("div");
  layer.className = "page-dissolve";
  layer.setAttribute("aria-hidden", "true");
  layer.inert = true;
  Object.assign(layer.style, { left: `${area.left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` });
  const copy = frozenCopy(page);
  Object.assign(copy.style, { position: "absolute", left: "0", top: `${area.top - top}px`, width: `${width}px`, height: `${area.height}px`, margin: "0" });
  layer.appendChild(copy);

  const from = origin ? { x: origin.x - area.left, y: origin.y - top } : { x: 0, y: height / 2 };
  const cells = dissolveCells(width, height, from);
  const paint = (elapsed: number) => {
    const mask = maskFrame(cells, elapsed, width);
    layer.style.setProperty("-webkit-mask", mask);
    layer.style.setProperty("mask", mask);
  };
  paint(0);
  document.body.appendChild(layer);

  let frame = 0;
  const began = performance.now();
  const end = () => {
    cancelAnimationFrame(frame);
    layer.remove();
    if (cancelRunning === end) cancelRunning = null;
  };
  const tick = (now: number) => {
    const elapsed = now - began;
    if (elapsed >= DURATION_MS) return end();
    paint(elapsed);
    frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
  cancelRunning = end;
}
