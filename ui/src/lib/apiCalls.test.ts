import { describe, expect, it } from "vitest";
import { forbiddenCall, httpCall } from "../test/apiCallFixtures";
import { bodyView, callFailed, curlCommand, exchangeText, httpCallCount, jsonTokens, matchesFilter, splitMasked, splitUrl, statusClass, statusText } from "./apiCalls";

describe("apiCalls", () => {
  it("pretty-prints a JSON body and keeps other text as sent", () => {
    expect(bodyView('{"a":1,"b":[true,null]}')).toEqual({ text: '{\n  "a": 1,\n  "b": [\n    true,\n    null\n  ]\n}', json: true, truncated: null });
    expect(bodyView("Forbidden")).toEqual({ text: "Forbidden", json: false, truncated: null });
    expect(bodyView("")).toBeNull();
    expect(bodyView(null)).toBeNull();
  });

  it("names how much of a long body the run left out", () => {
    expect(bodyView('{"a": "xyz...[truncated: 1200 more characters]')).toEqual({ text: '{"a": "xyz', json: false, truncated: 1200 });
  });

  it("colours JSON and marks a masked value", () => {
    const tokens = jsonTokens('{\n  "token": "[REDACTED]",\n  "id": 4,\n  "ok": true,\n  "name": "Jim"\n}');
    expect(tokens.filter((token) => token.kind !== "plain").map((token) => `${token.kind}:${token.text}`)).toEqual([
      'key:"token"',
      'masked:"[REDACTED]"',
      'key:"id"',
      "number:4",
      'key:"ok"',
      "literal:true",
      'key:"name"',
      'string:"Jim"',
    ]);
    expect(tokens.map((token) => token.text).join("")).toContain('"id": 4');
  });

  it("splits text at each mask", () => {
    expect(splitMasked("sid=[REDACTED]&x=******!")).toEqual(["sid=", null, "&x=", null, "!"]);
    expect(splitMasked("plain")).toEqual(["plain"]);
  });

  it("classes calls by status and flags the ones that need attention", () => {
    expect(statusClass(httpCall())).toBe("2");
    expect(callFailed(httpCall())).toBe(false);
    expect(statusClass(forbiddenCall())).toBe("4");
    expect(callFailed(forbiddenCall())).toBe(true);
    const timedOut = httpCall({ response: null, error: "timed out after 30 s" });
    expect(statusClass(timedOut)).toBe("error");
    expect(statusText(timedOut)).toBe("No response");
    expect(callFailed(httpCall({ checks: [{ ...httpCall().checks[0], verdict: "failed" }] }))).toBe(true);
  });

  it("filters by text and status class", () => {
    expect(matchesFilter(httpCall(), "auth", "all")).toBe(true);
    expect(matchesFilter(httpCall(), "post", "2")).toBe(true);
    expect(matchesFilter(httpCall(), "", "4")).toBe(false);
    expect(matchesFilter(forbiddenCall(), "403", "4")).toBe(true);
    expect(matchesFilter(httpCall({ response: null, error: "refused" }), "", "5")).toBe(true);
  });

  it("copies as cURL with masked values left masked", () => {
    expect(curlCommand(httpCall())).toBe(
      "curl -X POST 'https://restful-booker.herokuapp.com/auth' \\\n  -H 'Content-Type: application/json' \\\n  --data-raw '{\"username\": \"admin\", \"password\": \"[REDACTED]\"}'",
    );
    expect(curlCommand(httpCall({ request: { headers: [], body: "it's" } }))).toContain(`--data-raw 'it'\\''s'`);
    const text = exchangeText(forbiddenCall());
    expect(text).toContain("=== REQUEST ===\nPUT https://restful-booker.herokuapp.com/booking/1");
    expect(text).toContain("=== RESPONSE ===\n403 Forbidden (391 ms)");
  });

  it("splits a URL and counts the calls the steps name", () => {
    expect(splitUrl("https://api.example.com/booking/1?x=2")).toEqual({ host: "api.example.com", path: "/booking/1?x=2" });
    expect(splitUrl("not a url")).toEqual({ host: "", path: "not a url" });
    const ref = "hercules://runs/r/steps/1/http.json";
    expect(httpCallCount([{ evidence: [ref] }, { evidence: [ref, "hercules://runs/r/steps/2/checks.json"] }, { evidence: [] }])).toBe(1);
  });
});
