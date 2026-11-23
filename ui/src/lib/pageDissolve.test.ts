import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DURATION_MS, TILE_PX, cellAlpha, dissolveCells, dissolvePage, maskFrame } from "./pageDissolve";

const fixed = () => 0.5;

describe("the page dissolve's blocks", () => {
  it("cover the area in 48px blocks", () => {
    const cells = dissolveCells(1000, 500, { x: 0, y: 250 }, { random: fixed });
    expect(TILE_PX).toBe(48);
    expect(cells).toHaveLength(Math.ceil(1000 / 48) * Math.ceil(500 / 48));
    expect(cells[1]).toMatchObject({ x: 48, y: 0 });
  });

  it("start later the farther they are from the click, all within the duration", () => {
    const cells = dissolveCells(960, 480, { x: 0, y: 0 }, { random: fixed });
    const near = cells[0];
    const far = cells[cells.length - 1];
    expect(near.start).toBeLessThan(far.start);
    expect(Math.max(...cells.map((cell) => cell.start))).toBeLessThanOrEqual(DURATION_MS);
  });

  it("start from the click when it is inside, and from the edge when it is beside the area", () => {
    const middle = dissolveCells(960, 480, { x: 480, y: 240 }, { random: fixed });
    const centre = middle.reduce((best, cell) => (cell.start < best.start ? cell : best));
    expect(centre).toMatchObject({ x: 432, y: 192 });
    const beside = dissolveCells(960, 480, { x: -150, y: 0 }, { random: fixed });
    expect(beside.reduce((best, cell) => (cell.start < best.start ? cell : best))).toMatchObject({ x: 0, y: 0 });
  });

  it("fade smoothly from fully shown to gone", () => {
    const cell = { x: 0, y: 0, start: 100 };
    expect(cellAlpha(cell, 50)).toBe(1);
    expect(cellAlpha(cell, 100 + (DURATION_MS * 0.32) / 2)).toBeCloseTo(0.5);
    expect(cellAlpha(cell, 400)).toBe(0);
  });

  it("draw only the blocks still showing into the mask, one layer per run at the same opacity", () => {
    const row = [
      { x: 0, y: 0, start: 1000 },
      { x: 48, y: 0, start: 1000 },
      { x: 96, y: 0, start: 0 },
      { x: 144, y: 0, start: 1000 },
    ];
    const mask = maskFrame(row, 200, 192);
    expect(mask.split("linear-gradient(").length - 1).toBe(2);
    expect(mask).toContain("0px 0px / 96px 48px no-repeat");
    expect(mask).toContain("144px 0px / 48px 48px no-repeat");
    expect(mask).not.toContain("url(");
    expect(maskFrame(row, 5000, 192)).toBe("linear-gradient(transparent, transparent)");
  });

  it("start a new layer at each row", () => {
    const cells = [
      { x: 0, y: 0, start: 1000 },
      { x: 0, y: 48, start: 1000 },
    ];
    expect(maskFrame(cells, 0, 48).split("linear-gradient(").length - 1).toBe(2);
  });
});

describe("dissolvePage", () => {
  let frames: FrameRequestCallback[];
  let reduced: boolean;

  beforeEach(() => {
    frames = [];
    reduced = false;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: reduced && query.includes("reduce") }));
    document.body.innerHTML = '<main id="main"><h1 id="title">Runs</h1><label for="q">Search</label><input id="q"></main>';
    const main = document.getElementById("main") as HTMLElement;
    main.getBoundingClientRect = () => ({ left: 208, top: 0, right: 1208, bottom: 700, width: 1000, height: 700, x: 208, y: 0, toJSON: () => ({}) });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  const run = (until: number) => {
    let now = performance.now();
    while (frames.length) {
      now += 16;
      frames.shift()?.(Math.min(now, performance.now() + until));
    }
  };

  it("lays a masked copy of the page over it, without ids, and removes it when done", () => {
    dissolvePage(document.getElementById("main"), { x: 100, y: 300 });
    const layer = document.querySelector(".page-dissolve") as HTMLElement;
    expect(layer).toBeTruthy();
    expect(layer.getAttribute("aria-hidden")).toBe("true");
    expect(layer.style.left).toBe("208px");
    expect(layer.style.width).toBe("1000px");
    expect(layer.querySelector("h1")?.textContent).toBe("Runs");
    expect(layer.querySelectorAll("[id]")).toHaveLength(0);
    expect(document.querySelectorAll("#title")).toHaveLength(1);
    run(DURATION_MS + 50);
    expect(document.querySelector(".page-dissolve")).toBeNull();
  });

  it("ends a running dissolve when another starts", () => {
    dissolvePage(document.getElementById("main"), null);
    dissolvePage(document.getElementById("main"), null);
    expect(document.querySelectorAll(".page-dissolve")).toHaveLength(1);
  });

  it("does nothing when the viewer asks for reduced motion", () => {
    reduced = true;
    dissolvePage(document.getElementById("main"), { x: 100, y: 300 });
    expect(document.querySelector(".page-dissolve")).toBeNull();
  });
});
