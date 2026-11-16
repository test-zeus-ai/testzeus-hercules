import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { sampleView, samplePlan } from "../test/planFixtures";
import { PlanPanel } from "./PlanPanel";
import { PlanReview } from "./PlanReview";

describe("PlanPanel", () => {
  it("shows plan steps with author and inferred criteria, results, risks, lint and amendments as a diff", () => {
    render(<PlanPanel plan={samplePlan()} />);
    const dialog = screen.getByText('the "New Record" dialog is open').closest("li") as HTMLElement;
    expect(within(dialog).getByText("inferred")).toBeTruthy();
    expect(within(dialog).getByText("recovered")).toBeTruthy();
    const created = screen.getByText('the page shows "was created"').closest("li") as HTMLElement;
    expect(within(created).getByText("author")).toBeTruthy();
    expect(within(created).getByTitle("Failed")).toBeTruthy();
    expect(screen.getByText(/irreversible/).getAttribute("title")).toBe("saves the record");
    expect(screen.getByText(/looks cut off/).textContent).toContain('the value "Acme" looks cut off');
    const amendments = screen.getByRole("list", { name: "Amendments of step 1" });
    expect(within(amendments).getByText("recovery")).toBeTruthy();
    expect(amendments.querySelector("ins")?.textContent).toBe('click button "New"');
  });

  it("gives a plan step without a criterion result its step's outcome in the run", () => {
    const { container, rerender } = render(<PlanPanel plan={samplePlan()} />);
    const fill = () => container.querySelector('[data-plan-step="2.1"]') as HTMLElement;
    expect(fill().className).toContain("plan-step-pending");
    rerender(<PlanPanel plan={samplePlan()} stepStatus={{ 1: "passed", 2: "passed" }} />);
    expect(fill().className).toContain("plan-step-passed");
    rerender(<PlanPanel plan={samplePlan()} stepStatus={{ 1: "passed", 2: "skipped" }} />);
    expect(fill().className).toContain("plan-step-not_run");
  });
});

describe("PlanReview", () => {
  it("approves or rejects the plan", () => {
    const onDecide = vi.fn();
    render(<PlanReview view={sampleView()} busy={false} error={null} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve and run/ }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onDecide.mock.calls).toEqual([[{ decision: "approve" }], [{ decision: "reject" }]]);
  });

  it("edits a step, a criterion's origin and the order, then sends the edited plan", () => {
    const onDecide = vi.fn();
    render(<PlanReview view={sampleView()} busy={false} error={null} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit plan" }));
    fireEvent.change(screen.getByLabelText("Plan step 2.1"), { target: { value: 'fill "Record Name" with "Acme Corp"' } });
    fireEvent.click(screen.getAllByRole("button", { name: "Make author" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Move step 2 up" }));
    fireEvent.click(screen.getByRole("button", { name: /Save and approve/ }));
    const [[decision]] = onDecide.mock.calls;
    expect(decision.decision).toBe("edit");
    expect(decision.plan.steps[1].text).toBe('fill "Record Name" with "Acme Corp"');
    expect(decision.plan.steps[0].criteria[0].origin).toBe("author");
    expect(decision.plan.order).toEqual([2, 1]);
  });

  it("refuses to send an edit the daemon would refuse", () => {
    const onDecide = vi.fn();
    render(<PlanReview view={sampleView()} busy={false} error="the edited plan cannot run" onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit plan" }));
    fireEvent.change(screen.getByLabelText("Plan step 1.1"), { target: { value: "" } });
    expect((screen.getByRole("button", { name: /Save and approve/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByRole("alert").map((node) => node.textContent)).toEqual(["Plan step 1.1 needs a text.", "the edited plan cannot run"]);
  });
});
