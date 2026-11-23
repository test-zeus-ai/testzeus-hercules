import type { HttpCall } from "../lib/apiCalls";

export function httpCall(overrides: Partial<HttpCall> = {}): HttpCall {
  return {
    step: 1,
    action: 'I send a POST request to "/auth"',
    evidence: "steps/1/http.json",
    method: "POST",
    url: "https://restful-booker.herokuapp.com/auth",
    duration_ms: 1357,
    error: null,
    request: { headers: [["Content-Type", "application/json"]], body: '{"username": "admin", "password": "[REDACTED]"}' },
    response: { status: 200, reason: "OK", headers: [["content-type", "application/json; charset=utf-8"]], body: '{"token": "[REDACTED]"}', bytes: 27 },
    checks: [
      { step: 2, check: "http_status", expected: "200", actual: "200 OK", verdict: "passed", text: "the response status code should be 200" },
      { step: 3, check: "json_not_empty", expected: "$.token", actual: "[REDACTED]", verdict: "passed", text: 'the response JSON "$.token" should not be empty' },
    ],
    saves: [{ step: 4, name: "authToken", value: "[REDACTED]" }],
    used_by: [1, 2, 3, 4],
    ...overrides,
  };
}

export function forbiddenCall(): HttpCall {
  return httpCall({
    step: 5,
    action: "I update booking 1 without the token",
    evidence: "steps/5/http.json",
    method: "PUT",
    url: "https://restful-booker.herokuapp.com/booking/1",
    duration_ms: 391,
    request: { headers: [["Content-Type", "application/json"]], body: '{"firstname": "James"}' },
    response: { status: 403, reason: "Forbidden", headers: [["content-type", "text/plain"]], body: "Forbidden", bytes: 9 },
    checks: [{ step: 5, check: "http_status", expected: "200", actual: "403 Forbidden", verdict: "failed", text: "status 200" }],
    saves: [],
    used_by: [5],
  });
}
