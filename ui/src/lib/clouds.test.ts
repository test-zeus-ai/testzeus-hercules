import { describe, expect, it } from "vitest";
import {
  EMPTY_CLOUD_RUN_FORM,
  cloudBundleUrl,
  cloudMonogram,
  cloudRunAnnouncement,
  cloudRunBlocker,
  cloudRunBody,
  cloudRunOptionsLabel,
  cloudRunSeconds,
  cloudRunsPollInterval,
  connectionLabel,
  defaultCloud,
  validateCloudRunForm,
} from "./clouds";
import { cloud } from "../test/cloudFixtures";

describe("cloud labels", () => {
  it("makes a two-letter monogram from the name", () => {
    expect(cloudMonogram("BrowserStack")).toBe("BS");
    expect(cloudMonogram("Sauce Labs")).toBe("SL");
    expect(cloudMonogram("TestMu AI")).toBe("TM");
    expect(cloudMonogram("acme")).toBe("AC");
  });

  it("says who is connected and where the keys come from", () => {
    expect(connectionLabel(cloud())).toBe("Connected as alice");
    expect(connectionLabel(cloud({ source: "env" }))).toBe("Connected as alice · from environment");
    expect(connectionLabel(cloud({ connected: false, source: "none", username: null }))).toBe("Not connected");
  });
});

describe("cloudRunBlocker", () => {
  it("is null when the cloud can run this kind of test", () => {
    expect(cloudRunBlocker(cloud(), "web", true)).toBeNull();
  });

  it("puts the egress policy first, then the connection, then readiness for the kind", () => {
    expect(cloudRunBlocker(cloud({ connected: false }), "web", false)).toMatch(/egress policy is local/);
    expect(cloudRunBlocker(cloud({ connected: false }), "web", true)).toBe("Not connected yet.");
    expect(cloudRunBlocker(cloud({ connected: false, problem: "the access key is empty" }), "web", true)).toBe("the access key is empty");
    expect(cloudRunBlocker(cloud({ web_ready: "saucectl is not installed" }), "web", true)).toBe("saucectl is not installed");
    expect(cloudRunBlocker(cloud({ web_ready: "saucectl is not installed" }), "mobile", true)).toBeNull();
    expect(cloudRunBlocker(cloud({ mobile_ready: "pip install Appium-Python-Client" }), "mobile", true)).toBe("pip install Appium-Python-Client");
  });

  it("picks the first ready cloud by default, else the first", () => {
    const clouds = [cloud({ connected: false }), cloud({ id: "saucelabs", name: "Sauce Labs" })];
    expect(defaultCloud(clouds, "web", true)).toBe("saucelabs");
    expect(defaultCloud(clouds, "web", false)).toBe("browserstack");
    expect(defaultCloud([], "web", true)).toBeNull();
  });
});

describe("cloud run requests", () => {
  const form = { ...EMPTY_CLOUD_RUN_FORM, browser: "edge", os: "macos", device: " Pixel 8 ", platformVersion: "" };

  it("sends browser and OS for a web test, and only the set device fields for an app", () => {
    expect(cloudRunBody("browserstack", form, "web")).toEqual({ cloud: "browserstack", browser: "edge", os: "macos" });
    expect(cloudRunBody("saucelabs", form, "mobile")).toEqual({ cloud: "saucelabs", device: "Pixel 8" });
  });

  it("links the bundle with the same options, encoded", () => {
    expect(cloudBundleUrl("r 1", "testmu", form, "web")).toBe("/api/runs/r%201/cloud-bundle?cloud=testmu&browser=edge&os=macos");
    expect(cloudBundleUrl("r_1", "saucelabs", form, "mobile")).toBe("/api/runs/r_1/cloud-bundle?cloud=saucelabs&device=Pixel+8");
  });

  it("checks device and version like the daemon does", () => {
    expect(validateCloudRunForm({ ...form, device: "Pixel 8 (5G)", platformVersion: "14.1" }, "mobile")).toEqual({});
    const errors = validateCloudRunForm({ ...form, device: "Pixel; rm", platformVersion: "14 beta" }, "mobile");
    expect(Object.keys(errors).sort()).toEqual(["device", "platformVersion"]);
    expect(validateCloudRunForm({ ...form, device: "Pixel; rm" }, "web")).toEqual({});
  });
});

describe("cloud run display", () => {
  it("labels the options a run used", () => {
    expect(cloudRunOptionsLabel({ kind: "web", options: { browser: "chrome", os: "windows" } })).toBe("Chrome · Windows");
    expect(cloudRunOptionsLabel({ kind: "mobile", options: { device: "Pixel 8", platform_version: "14" } })).toBe("Pixel 8 · version 14");
    expect(cloudRunOptionsLabel({ kind: "mobile", options: {} })).toBe("The run's device");
  });

  it("measures elapsed time from the start, up to now while running", () => {
    const run = { created_at: "2026-11-12T10:00:00Z", started_at: "2026-11-12T10:00:05Z", finished_at: null };
    expect(cloudRunSeconds(run, Date.parse("2026-11-12T10:01:05Z"))).toBe(60);
    expect(cloudRunSeconds({ ...run, finished_at: "2026-11-12T10:00:15Z" })).toBe(10);
    expect(cloudRunSeconds({ ...run, started_at: null, created_at: "bad" })).toBeNull();
  });

  it("polls only while a run is active", () => {
    expect(cloudRunsPollInterval([{ status: "passed" }, { status: "running" }])).toBe(1500);
    expect(cloudRunsPollInterval([{ status: "passed" }, { status: "cancelled" }])).toBe(false);
    expect(cloudRunsPollInterval(undefined)).toBe(false);
  });

  it("announces status changes in words", () => {
    expect(cloudRunAnnouncement({ cloud_name: "Sauce Labs", status: "passed" })).toBe("Sauce Labs run passed");
    expect(cloudRunAnnouncement({ cloud_name: "Sauce Labs", status: "error" })).toBe("Sauce Labs run could not run");
  });
});
