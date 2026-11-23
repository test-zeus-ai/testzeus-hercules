import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, BLOCKED_EVIDENCE_URL, INVALID_RESPONSE, api, evidenceUrl } from "./api";
import { isCaseList, isModelsReport, isRunCase, isRunList, isRunView, isSavedCase } from "./guards";

function respond(body: unknown, status = 200) {
  return Promise.resolve(new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api() payload guards", () => {
  it("returns a payload that passes its guard", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond({ run_id: "r_1", status: "running", result: null })));
    await expect(api("/api/runs/r_1", { guard: isRunView })).resolves.toMatchObject({ run_id: "r_1" });
  });

  it("throws a clear ApiError when the payload has the wrong shape", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond("<html>proxy error</html>")));
    const error = await api("/api/runs/r_1?x=1", { guard: isRunView }).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(INVALID_RESPONSE);
    expect((error as ApiError).message).toMatch(/unexpected response from the daemon for \/api\/runs\/r_1\./i);
  });

  it("still reports HTTP errors before checking the shape", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond({ error: "run not found" }, 404)));
    await expect(api("/api/runs/x", { guard: isRunView })).rejects.toMatchObject({ status: 404, message: "run not found" });
  });
});

describe("guards", () => {
  it("check the run, its result and the run list", () => {
    expect(isRunView({ run_id: "r", status: "passed", result: { status: "passed", steps: [{ index: 1 }] } })).toBe(true);
    expect(isRunView({ run_id: "r", status: "passed", result: { status: "passed", steps: "none" } })).toBe(false);
    expect(isRunView({ status: "passed" })).toBe(false);
    expect(isRunList({ runs: [{ run_id: "r", status: "queued" }] })).toBe(true);
    expect(isRunList({ runs: [{ run_id: 3 }] })).toBe(false);
    expect(isRunList([])).toBe(false);
  });

  it("check cases", () => {
    const testCase = { id: "c", title: "C", steps: [{ action: "Open /" }] };
    expect(isRunCase({ test_case: testCase })).toBe(true);
    expect(isRunCase({ test_case: { steps: [{}] } })).toBe(false);
    expect(isSavedCase({ name: "c.json", kind: "json", source: null, test_case: testCase })).toBe(true);
    expect(isSavedCase({ name: "c.json", kind: "json", source: 1, test_case: testCase })).toBe(false);
    expect(isCaseList({ cases: [{ name: "c.json", path: ".hercules/cases/c.json", valid: true }] })).toBe(true);
    expect(isCaseList({ cases: [{ name: "c.json" }] })).toBe(false);
  });

  it("check the models report", () => {
    expect(isModelsReport({ provider: "local", model: "m", capabilities: [], modes: { pure: {}, hybrid: {} } })).toBe(true);
    expect(isModelsReport({ provider: "local", model: "m", capabilities: [] })).toBe(false);
  });
});

describe("evidenceUrl", () => {
  it("encodes each segment of a run's reference", () => {
    expect(evidenceUrl("r_1", "hercules://runs/r_1/steps/2/som.jpg")).toBe("/api/runs/r_1/evidence/steps/2/som.jpg");
    expect(evidenceUrl("r_1", "steps/2/a b#c.jpg")).toBe("/api/runs/r_1/evidence/steps/2/a%20b%23c.jpg");
  });

  it("refuses references that would leave the run directory", () => {
    for (const ref of ["../secrets.txt", "steps/../../x", "hercules://runs/r_1/./x.jpg", "steps//x.jpg", "steps\\..\\x", "hercules://runs/other/../x"]) {
      expect(evidenceUrl("r_1", ref), ref).toBe(BLOCKED_EVIDENCE_URL);
    }
  });
});
