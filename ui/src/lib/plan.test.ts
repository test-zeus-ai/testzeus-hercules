import { describe, expect, it } from "vitest";
import { samplePlan } from "../test/planFixtures";
import { editProblems, moveSourceStep, planGroups, planStepState, removeCriterion, setCriterionText, setStepText, toggleOrigin } from "./plan";

describe("plan", () => {
  it("groups plan steps under their source steps in run order", () => {
    const plan = samplePlan();
    expect(planGroups(plan).map(({ source, steps }) => [source.number, steps.map((step) => step.id)])).toEqual([
      [1, ["1.1"]],
      [2, ["2.1", "2.2"]],
    ]);
    expect(planGroups(moveSourceStep(plan, 2, -1)).map(({ source }) => source.number)).toEqual([2, 1]);
    expect(moveSourceStep(plan, 1, -1)).toBe(plan);
  });

  it("derives each plan step's state from its criteria and amendments", () => {
    const plan = samplePlan();
    expect(planStepState(plan, plan.steps[0])).toBe("passed");
    expect(planStepState(plan, plan.steps[2])).toBe("failed");
    expect(planStepState(plan, plan.steps[1])).toBe("pending");
    const unmet = samplePlan({ results: [{ ...plan.results![0], status: "unverified" }] });
    expect(planStepState(unmet, unmet.steps[0])).toBe("unmet");
  });

  it("edits step text, criteria and origin without touching anything else", () => {
    const plan = samplePlan();
    const edited = toggleOrigin(setStepText(plan, "2.1", 'fill "Record Name" with "Acme Corp"'), "1.1.c1");
    expect(edited.steps[1].text).toBe('fill "Record Name" with "Acme Corp"');
    expect(edited.steps[0].criteria![0].origin).toBe("author");
    expect(plan.steps[1].text).toBe('fill "Record Name" with "Acme"');
    const reworded = setCriterionText(plan, "1.1.c1", "a form for the new record is shown");
    expect(reworded.steps[0].criteria![0]).toMatchObject({ check: "judged", expected: "a form for the new record is shown", target: null, state: null });
    expect(removeCriterion(plan, "2.2.c1").steps[2].criteria).toEqual([]);
  });

  it("names what the daemon would refuse in an edit", () => {
    const plan = samplePlan();
    expect(editProblems(plan)).toEqual([]);
    expect(editProblems(setStepText(plan, "1.1", " "))).toEqual(["Plan step 1.1 needs a text."]);
    expect(editProblems({ ...plan, steps: plan.steps.filter((step) => step.step !== 1) })).toEqual(["Step 1 has no plan step."]);
  });
});
