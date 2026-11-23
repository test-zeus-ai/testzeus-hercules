import { afterEach, describe, expect, it } from "vitest";
import { availableActions, shortcutAction } from "./control";
import { FRAME_HEADER_BYTES, FpsMeter, parseFrame, screencastUrl } from "./screencast";
import { applyEvent, initialTimeline, orderedSteps, replay } from "./timeline";
import type { RunEvent } from "./types";

const RUN = "r_20261026_101500_abc123";
const shot = `hercules://runs/${RUN}/steps/3/plain.jpg`;

// A tester pauses before step 2, steps once, resumes; step 3 asks before a destructive click.
const EVENTS: RunEvent[] = [
  { seq: 1, kind: "status", data: { status: "running" } },
  { seq: 2, kind: "run_started", data: { steps: 4 } },
  { seq: 3, kind: "step_started", data: { step: 1, action: "Open /cart" } },
  { seq: 4, kind: "step_finished", data: { step: 1, status: "passed", duration_ms: 400 } },
  { seq: 5, kind: "paused", data: { step: 2, where: "step", action: "Fill quantity", state: "paused" } },
  { seq: 6, kind: "status", data: { status: "paused", reason: "paused before step 2" } },
];

describe("control state in the timeline", () => {
  it("knows where the run is paused, without touching the step's status", () => {
    const state = replay(EVENTS);
    expect(state.control.paused).toEqual({ step: 2, where: "step", action: "Fill quantity" });
    expect(state.runStatus).toBe("paused");
    expect(orderedSteps(state)[1].status).toBe("pending");
  });

  it("clears the pause when the run resumes, and tracks a step run again", () => {
    let state = replay(EVENTS);
    state = applyEvent(state, { seq: 7, kind: "resumed", data: { step: 2, state: "running", stepping: true } });
    state = applyEvent(state, { seq: 8, kind: "status", data: { status: "running" } });
    expect(state.control.paused).toBeNull();
    state = applyEvent(state, { seq: 9, kind: "step_started", data: { step: 2, action: "Fill quantity" } });
    state = applyEvent(state, { seq: 10, kind: "step_finished", data: { step: 2, status: "failed" } });
    state = applyEvent(state, { seq: 11, kind: "step_started", data: { step: 2, action: "Fill quantity 2", retry: true } });
    const step = orderedSteps(state)[1];
    expect(step.status).toBe("running");
    expect(step.action).toBe("Fill quantity 2");
    expect(step.attempts).toBe(1);
  });

  it("holds the approval question until it is answered", () => {
    let state = replay(EVENTS);
    state = applyEvent(state, {
      seq: 7,
      kind: "needs_approval",
      data: { id: "a1b2", kind: "destructive", step: 3, reason: 'about to click "Remove"', action: 'click button "Remove"', target: "Remove", screenshot: shot, timeout_s: 600, allow_edit: true },
    });
    expect(state.control.approval).toMatchObject({ id: "a1b2", kind: "destructive", step: 3, screenshot: shot, allowEdit: true, timeoutS: 600 });
    expect(state.latestScreenshot?.ref).toBe(shot);
    state = applyEvent(state, { seq: 8, kind: "approval_result", data: { id: "a1b2", decision: "approve", state: "running" } });
    expect(state.control.approval).toBeNull();
    expect(state.control.notice).toBe("Approved");
  });

  it("an approval for a draft (step 0) does not create a step", () => {
    const state = applyEvent(initialTimeline(), { seq: 1, kind: "needs_approval", data: { id: "d", kind: "draft", step: 0, reason: "the case is a draft", allow_edit: false } });
    expect(state.control.approval?.kind).toBe("draft");
    expect(orderedSteps(state)).toEqual([]);
  });

  it("reports input results, ignored commands and a resumed run", () => {
    let state = replay(EVENTS);
    state = applyEvent(state, { seq: 7, kind: "input_result", data: { ok: true, kind: "click", detail: "clicked at (640, 400)" } });
    expect(state.control.lastInput).toEqual({ ok: true, detail: "clicked at (640, 400)" });
    state = applyEvent(state, { seq: 8, kind: "control_ignored", data: { reason: "skip only applies while the run is paused" } });
    expect(state.control.notice).toBe("skip only applies while the run is paused");
    state = applyEvent(state, { seq: 9, kind: "run_resumed", data: { from_step: 3 } });
    expect(state.control.resumedFrom).toBe(3);
  });

  it("forgets pauses and questions when the run ends", () => {
    let state = applyEvent(replay(EVENTS), { seq: 7, kind: "needs_approval", data: { id: "x", kind: "recovery_exhausted", step: 2, reason: "step 2 failed" } });
    state = applyEvent(state, { seq: 8, kind: "status", data: { status: "cancelled" } });
    expect(state.control).toMatchObject({ paused: null, approval: null });
  });
});

describe("debug actions", () => {
  it("offers what the state allows", () => {
    expect(availableActions("running")).toEqual(["pause", "step"]);
    expect(availableActions("paused")).toEqual(["resume", "step", "skip", "retry"]);
    expect(availableActions("needs_approval")).toEqual(["pause"]);
    expect(availableActions("passed")).toEqual([]);
    expect(availableActions("queued")).toEqual([]);
  });

  it("maps space and n to pause/resume and step, never while typing", () => {
    const key = (key: string, extra: Partial<KeyboardEvent> = {}, target: EventTarget = document.body) => ({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...extra, target }) as unknown as KeyboardEvent;
    expect(shortcutAction(key(" "), "running")).toBe("pause");
    expect(shortcutAction(key(" "), "paused")).toBe("resume");
    expect(shortcutAction(key("n"), "paused")).toBe("step");
    expect(shortcutAction(key("N"), "running")).toBe("step");
    expect(shortcutAction(key(" "), "passed")).toBeNull();
    expect(shortcutAction(key(" ", {}, document.createElement("input")), "running")).toBeNull();
    expect(shortcutAction(key(" ", {}, document.createElement("textarea")), "running")).toBeNull();
    expect(shortcutAction(key("n", { metaKey: true }), "paused")).toBeNull();
    expect(shortcutAction(key("n", { shiftKey: true }), "paused")).toBeNull();
    expect(shortcutAction(key(" ", { repeat: true }), "paused")).toBeNull();
    expect(shortcutAction(key(" ", { defaultPrevented: true }), "paused")).toBeNull();
    expect(shortcutAction(key("x"), "paused")).toBeNull();
  });

  describe("where the focus is", () => {
    const press = (target: EventTarget, scope?: Element | null) =>
      shortcutAction({ key: " ", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, target } as unknown as KeyboardEvent, "running", scope);

    function mount(html: string): HTMLElement {
      const main = document.createElement("main");
      main.innerHTML = html;
      document.body.appendChild(main);
      return main;
    }

    afterEach(() => {
      document.body.innerHTML = "";
    });

    it("never fires on a control, whatever its kind", () => {
      const main = mount(`
        <button id="b">Go</button>
        <a id="a" href="/x"><span id="inner">link</span></a>
        <input id="cb" type="checkbox" />
        <input id="radio" type="radio" />
        <div id="tab" role="tab">Plan</div>
        <div id="rb" role="button">Fake</div>
        <div id="rcb" role="checkbox">Fake</div>
        <div id="rr" role="radio">Fake</div>
        <details><summary id="sum">More</summary></details>
        <div id="edit" contenteditable="true">notes</div>`);
      for (const id of ["b", "inner", "cb", "radio", "tab", "rb", "rcb", "rr", "sum"]) {
        expect(press(main.querySelector(`#${id}`)!, main), id).toBeNull();
      }
      const editable = main.querySelector("#edit") as HTMLElement;
      Object.defineProperty(editable, "isContentEditable", { value: true });
      expect(press(editable, main)).toBeNull();
    });

    it("fires on the page body and on plain content of the run page", () => {
      const main = mount(`<section><p id="text" tabindex="-1">Step 2</p></section>`);
      expect(press(document.body, main)).toBe("pause");
      expect(press(main, main)).toBe("pause");
      expect(press(main.querySelector("#text")!, main)).toBe("pause");
    });

    it("ignores focus outside the run page's main region", () => {
      const main = mount(`<p>run</p>`);
      const aside = document.createElement("aside");
      aside.innerHTML = `<p id="side" tabindex="-1">nav</p>`;
      document.body.appendChild(aside);
      expect(press(aside.querySelector("#side")!, main)).toBeNull();
    });

    it("stays quiet while a modal is open, even with the focus on the body", () => {
      const main = mount(`<p id="text" tabindex="-1">run</p><dialog open><button>Close</button></dialog>`);
      expect(press(document.body, main)).toBeNull();
      expect(press(main.querySelector("#text")!, main)).toBeNull();
      expect(press(main.querySelector("dialog button")!, main)).toBeNull();
    });
  });
});

describe("screencast frames", () => {
  function packet(width: number, height: number, jpeg: number[]): ArrayBuffer {
    const buffer = new ArrayBuffer(FRAME_HEADER_BYTES + jpeg.length);
    const view = new DataView(buffer);
    view.setUint32(0, jpeg.length);
    view.setUint16(4, width);
    view.setUint16(6, height);
    view.setFloat64(8, 1761444000.5);
    new Uint8Array(buffer, FRAME_HEADER_BYTES).set(jpeg);
    return buffer;
  }

  it("parses the header and the JPEG", () => {
    const frame = parseFrame(packet(1280, 800, [0xff, 0xd8, 1, 2, 3]));
    expect(frame).not.toBeNull();
    expect(frame).toMatchObject({ width: 1280, height: 800, captured: 1761444000.5 });
    expect(Array.from(frame!.jpeg)).toEqual([0xff, 0xd8, 1, 2, 3]);
  });

  it("rejects truncated or inconsistent frames", () => {
    expect(parseFrame(new ArrayBuffer(4))).toBeNull();
    const broken = packet(10, 10, [0xff, 0xd8]);
    new DataView(broken).setUint32(0, 99);
    expect(parseFrame(broken)).toBeNull();
  });

  it("measures frames per second over a sliding window", () => {
    const meter = new FpsMeter(2000);
    for (let ms = 0; ms <= 2000; ms += 200) meter.tick(ms);
    expect(meter.fps(2000)).toBeCloseTo(5, 0);
    expect(meter.fps(6000)).toBe(0);
  });

  it("builds a same-origin WebSocket URL", () => {
    expect(screencastUrl("r_1", { protocol: "http:", host: "127.0.0.1:7373" })).toBe("ws://127.0.0.1:7373/api/runs/r_1/screencast");
    expect(screencastUrl("r 2", { protocol: "https:", host: "localhost" })).toBe("wss://localhost/api/runs/r%202/screencast");
  });
});
