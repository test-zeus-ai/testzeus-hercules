import { describe, expect, it } from "vitest";
import { cloudDevices } from "../test/cloudFixtures";
import { EMPTY_DESKTOP_FILTER, EMPTY_MOBILE_FILTER, browserIcon, browserLabel, desktopOses, devicesSummary, filterDesktop, filterMobile, mixesRealAndVirtual, osIcon, versionsSummary } from "./cloudDevices";

const catalog = cloudDevices();

describe("cloud device lists", () => {
  it("filters mobile devices by platform, type, real or virtual, and words", () => {
    expect(filterMobile(catalog.mobile, EMPTY_MOBILE_FILTER)).toHaveLength(4);
    expect(filterMobile(catalog.mobile, { ...EMPTY_MOBILE_FILTER, platform: "ios" }).map((d) => d.name)).toEqual(["iPhone 16", "iPhone Simulator"]);
    expect(filterMobile(catalog.mobile, { ...EMPTY_MOBILE_FILTER, kind: "tablet" }).map((d) => d.name)).toEqual(["Samsung Galaxy Tab S9"]);
    expect(filterMobile(catalog.mobile, { ...EMPTY_MOBILE_FILTER, real: "virtual" }).map((d) => d.name)).toEqual(["iPhone Simulator"]);
    expect(filterMobile(catalog.mobile, { ...EMPTY_MOBILE_FILTER, query: "ios 18" }).map((d) => d.name)).toEqual(["iPhone 16"]);
    expect(filterMobile(catalog.mobile, { ...EMPTY_MOBILE_FILTER, query: "PIXEL" }).map((d) => d.name)).toEqual(["Google Pixel 9"]);
  });

  it("filters desktop browsers by OS and words, including browser names and versions", () => {
    expect(filterDesktop(catalog.desktop, { ...EMPTY_DESKTOP_FILTER, os: "macOS" }).map((b) => b.browser)).toEqual(["safari"]);
    expect(filterDesktop(catalog.desktop, { ...EMPTY_DESKTOP_FILTER, query: "Chrome 131" }).map((b) => b.os)).toEqual(["Windows"]);
    expect(filterDesktop(catalog.desktop, { ...EMPTY_DESKTOP_FILTER, query: "sonoma" })).toHaveLength(1);
    expect(desktopOses(catalog.desktop)).toEqual(["Windows", "macOS"]);
  });

  it("names browsers, picks icons and shortens long version lists", () => {
    expect([browserLabel("edge"), browserLabel("ie"), browserLabel("brave")]).toEqual(["Edge", "Internet Explorer", "Brave"]);
    expect([browserIcon("safari"), browserIcon("chrome"), browserIcon("opera")]).toEqual(["compass", "chrome", "envs"]);
    expect([osIcon("Windows"), osIcon("macOS"), osIcon("Linux")]).toEqual(["windows", "apple", "envs"]);
    expect(versionsSummary(["131", "130"])).toBe("131, 130");
    expect(versionsSummary(["latest", "131", "130", "129", "90"])).toBe("latest, 131, 130 … 90");
  });

  it("summarises the list and knows when it mixes real and virtual devices", () => {
    expect(devicesSummary(catalog)).toBe("4 mobile devices · 2 desktop browsers");
    expect(devicesSummary({ ...catalog, mobile: catalog.mobile.slice(0, 1), desktop: catalog.desktop.slice(0, 1) })).toBe("1 mobile device · 1 desktop browser");
    expect(mixesRealAndVirtual(catalog.mobile)).toBe(true);
    expect(mixesRealAndVirtual(catalog.mobile.filter((device) => device.real))).toBe(false);
  });
});
