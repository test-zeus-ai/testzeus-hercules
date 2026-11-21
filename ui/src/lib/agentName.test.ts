import { describe, expect, it } from "vitest";
import { agentName } from "./format";

describe("agentName", () => {
  it("names the known coding agents", () => {
    expect(agentName("claude-code")).toBe("Claude Code");
    expect(agentName("copilot")).toBe("GitHub Copilot");
    expect(agentName("gemini")).toBe("Gemini CLI");
  });

  it("falls back for unknown or missing agents", () => {
    expect(agentName("other")).toBe("a coding agent");
    expect(agentName(null)).toBe("a coding agent");
  });
});
