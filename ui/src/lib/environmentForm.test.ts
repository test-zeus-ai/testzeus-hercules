import { describe, expect, it } from "vitest";
import { EMPTY_ENV, envToForm, environmentBody, targetSummary, validateEnvironment, type EnvForm } from "./environmentForm";

const app = (fields: Partial<EnvForm>): EnvForm => ({ ...EMPTY_ENV, name: "android", target: "app", ...fields });

describe("environment form", () => {
  it("needs a base URL for a web app only", () => {
    expect(validateEnvironment({ ...EMPTY_ENV, name: "web" })).toHaveProperty("baseUrl");
    expect(validateEnvironment(app({ appPackage: "com.android.settings" }))).toEqual({});
  });

  it("checks the native app fields", () => {
    expect(Object.keys(validateEnvironment(app({})))).toEqual(["app"]);
    expect(validateEnvironment(app({ app: "../outside.apk" }))).toHaveProperty("app");
    expect(validateEnvironment(app({ app: "notes.txt" }))).toHaveProperty("app");
    expect(validateEnvironment(app({ app: "bs://abc" }))).toHaveProperty("cloud");
    expect(validateEnvironment(app({ app: "apps/demo.apk", cloud: "browserstack", appiumUrl: "http://127.0.0.1:4723" }))).toHaveProperty("appiumUrl");
    expect(validateEnvironment(app({ appPackage: "com.example", cloud: "saucelabs" }))).toHaveProperty("app");
    expect(validateEnvironment(app({ activity: ".Main", app: "a.apk" }))).toHaveProperty("activity");
    expect(validateEnvironment(app({ platform: "ios", bundleId: "com.apple.Preferences", platformVersion: "17.5" }))).toEqual({});
    expect(validateEnvironment(app({ appPackage: "com.example.app", appiumUrl: "http://user:pw@host:4723" }))).toHaveProperty("appiumUrl");
  });

  it("sends a native app without browser settings, and a web app without an app", () => {
    const native = environmentBody(app({ appPackage: "com.android.settings", activity: ".Settings", appDevice: "emulator-5554", viewport: "1280x800", device: "iPhone 15" }), false);
    expect(native).toMatchObject({ viewport: null, device: null, browser: null, base_url: null });
    expect(native.mobile).toEqual({
      platform: "android",
      app: null,
      package: "com.android.settings",
      activity: ".Settings",
      bundle_id: null,
      device: "emulator-5554",
      platform_version: null,
      appium_url: null,
      cloud: null,
    });
    const phone = environmentBody({ ...EMPTY_ENV, name: "phone", baseUrl: "http://localhost:8000", device: "iPhone 15", appPackage: "x.y" }, true);
    expect(phone).toMatchObject({ device: "iPhone 15", mobile: null, overwrite: true });
  });

  it("round-trips a saved environment and summarises its target", () => {
    const saved = { name: "android", base_url: null, mobile: { platform: "android" as const, package: "com.android.settings", device: "emulator-5554" }, secret_names: [] };
    expect(envToForm(saved)).toMatchObject({ target: "app", appPackage: "com.android.settings", appDevice: "emulator-5554" });
    expect(targetSummary(saved)).toBe("Android app com.android.settings · emulator-5554");
    expect(targetSummary({ name: "c", mobile: { platform: "ios", app: "bs://x", cloud: "browserstack" }, secret_names: [] })).toBe("iOS app bs://x · browserstack");
    expect(targetSummary({ name: "p", base_url: "http://shop.test", device: "Pixel 7", secret_names: [] })).toBe("http://shop.test · Pixel 7");
  });
});
