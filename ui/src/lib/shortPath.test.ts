import { describe, expect, it } from "vitest";
import { shortPath } from "./format";

describe("shortPath", () => {
  it("shows a project's run folder from .hercules on", () => {
    expect(shortPath("/Users/ana/work/shop/.hercules/runs/r_1")).toBe(".hercules/runs/r_1");
    expect(shortPath("/srv/ci/app/.hercules/runs/r_1")).toBe(".hercules/runs/r_1");
  });
  it("shows files in the home folder as ~/", () => {
    expect(shortPath("/Users/ana/.hercules/clouds.env")).toBe("~/.hercules/clouds.env");
    expect(shortPath("/home/ana/.hercules/clouds.env")).toBe("~/.hercules/clouds.env");
    expect(shortPath("C:\\Users\\ana\\.hercules\\clouds.env")).toBe("~/.hercules/clouds.env");
  });
  it("keeps other paths", () => {
    expect(shortPath("/etc/hercules/clouds.env")).toBe("/etc/hercules/clouds.env");
    expect(shortPath("~/.hercules/clouds.env")).toBe("~/.hercules/clouds.env");
    expect(shortPath(null)).toBe("");
  });
});
