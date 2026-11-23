import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { forbiddenCall, httpCall } from "../test/apiCallFixtures";
import { ApiCallCard, ApiCalls } from "./ApiCalls";

function renderCalls(selected: number | null = null) {
  const onSelect = vi.fn();
  const onStep = vi.fn();
  const view = render(<ApiCalls runId="r_1" calls={[httpCall(), forbiddenCall()]} more={false} selected={selected} onSelect={onSelect} onStep={onStep} />);
  return { onSelect, onStep, ...view };
}

describe("ApiCalls", () => {
  it("lists each call with its method, path, status and step", () => {
    renderCalls();
    const rows = screen.getAllByRole("option");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("POST");
    expect(rows[0].textContent).toContain("/auth");
    expect(rows[0].textContent).toContain("Step 1");
    expect(rows[0].textContent).toContain("200");
    expect(rows[1].className).toContain("api-row-failed");
    expect(screen.getByText(/2 of 2 calls/)).toBeTruthy();
  });

  it("opens on the response, with masked values shown as masked", () => {
    renderCalls();
    expect(screen.getByRole("tab", { name: "Response" }).getAttribute("aria-selected")).toBe("true");
    const body = document.querySelector(".api-body") as HTMLElement;
    expect(body.textContent).toContain('"token"');
    expect(within(body).getByText("masked")).toBeTruthy();
    expect(body.textContent).not.toContain("[REDACTED]");
  });

  it("shows the request, and the checks and saves that used the response", () => {
    const { onStep } = renderCalls();
    fireEvent.click(screen.getByRole("tab", { name: "Request" }));
    expect((document.querySelector(".api-body") as HTMLElement).textContent).toContain('"username": "admin"');
    fireEvent.click(screen.getByRole("tab", { name: /Checks/ }));
    const checks = screen.getAllByRole("listitem");
    expect(checks.map((item) => item.textContent)).toEqual([
      expect.stringContaining("the response status code should be 200"),
      expect.stringContaining('the response JSON "$.token" should not be empty'),
      expect.stringContaining("{{authToken}}"),
    ]);
    fireEvent.click(within(checks[2]).getByRole("button", { name: "Step 4" }));
    expect(onStep).toHaveBeenCalledWith(4);
  });

  it("selects a call from the list and with the arrow keys", () => {
    const { onSelect } = renderCalls(1);
    fireEvent.click(screen.getAllByRole("option")[1]);
    expect(onSelect).toHaveBeenCalledWith(5);
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "ArrowDown" });
    expect(onSelect).toHaveBeenLastCalledWith(5);
  });

  it("shows the selected failed call's failing check", () => {
    renderCalls(5);
    expect(screen.getByText("1 check failed")).toBeTruthy();
    expect(screen.getByText("403 Forbidden")).toBeTruthy();
  });

  it("filters by text and status", () => {
    renderCalls();
    fireEvent.click(screen.getByRole("button", { name: "4xx" }));
    expect(screen.getAllByRole("option")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.change(screen.getByLabelText("Filter API calls"), { target: { value: "nothing-like-this" } });
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText(/2 calls are hidden/)).toBeTruthy();
  });

  it("says why a call got no response", () => {
    render(<ApiCalls runId="r_1" calls={[httpCall({ response: null, error: "timed out after 30 s" })]} more={false} selected={null} onSelect={() => {}} onStep={() => {}} />);
    expect(screen.getByRole("tab", { name: "Request" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("tab", { name: "Response" }));
    expect(screen.getByText(/No response came back: timed out after 30 s/)).toBeTruthy();
  });
});

describe("ApiCallCard", () => {
  it("sums up the call a step sent and opens it in the API calls tab", () => {
    const onOpen = vi.fn();
    render(<ApiCallCard call={httpCall()} step={1} onOpen={onOpen} />);
    expect(screen.getByText("Sent by this step")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Open in API calls/ }));
    expect(onOpen).toHaveBeenCalled();
  });

  it("names the checks a later step ran on the response", () => {
    render(<ApiCallCard call={httpCall()} step={2} onOpen={() => {}} />);
    expect(screen.getByText("Uses the response of step 1")).toBeTruthy();
    expect(screen.getByText("1 check passed")).toBeTruthy();
  });
});
