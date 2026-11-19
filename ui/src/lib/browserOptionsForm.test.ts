import { describe, expect, it } from "vitest";
import { browserOptionsBody, browserOptionsToForm, browserSummary, validateBrowserOptions } from "./browserOptionsForm";

describe("browser options form", () => {
  it("round-trips saved options through the form, keeping untyped ones as JSON", () => {
    const saved = { locale: "en-GB", args: ["--lang=en-GB"], headless: false, geolocation: { latitude: 51.5, longitude: -0.12 }, permissions: ["geolocation"] };
    const form = browserOptionsToForm(saved);
    expect(form.locale).toBe("en-GB");
    expect(form.headed).toBe(true);
    expect(JSON.parse(form.more)).toEqual({ geolocation: { latitude: 51.5, longitude: -0.12 }, permissions: ["geolocation"] });
    expect(browserOptionsBody(form)).toEqual(saved);
  });

  it("sends nothing when nothing is set", () => {
    expect(browserOptionsBody(browserOptionsToForm(null))).toBeNull();
  });

  it("checks locale, switches and Firefox-only preferences", () => {
    const form = { ...browserOptionsToForm(null), locale: "english", args: "lang=de", more: '{"firefox_user_prefs": {"a": 1}}' };
    const errors = validateBrowserOptions(form, "chromium");
    expect(Object.keys(errors).sort()).toEqual(["bo.args", "bo.locale", "bo.more"]);
    expect(validateBrowserOptions({ ...browserOptionsToForm(null), more: '{"firefox_user_prefs": {"a": 1}}' }, "firefox")).toEqual({});
  });

  it("records a video unless the environment turns it off, and saves only turning it off", () => {
    expect(browserOptionsToForm(null).recordVideo).toBe(true);
    expect(browserOptionsToForm({ locale: "en-GB" }).recordVideo).toBe(true);
    expect(browserOptionsToForm({ record_video: true }).recordVideo).toBe(true);
    expect(browserOptionsToForm({ record_video: false }).recordVideo).toBe(false);
    expect(browserOptionsBody({ ...browserOptionsToForm(null), recordVideo: false })).toEqual({ record_video: false });
    expect(browserOptionsBody(browserOptionsToForm({ record_video: true }))).toBeNull();
  });

  it("summarises the browser for lists", () => {
    expect(browserSummary("firefox", { locale: "de-DE" })).toBe("Firefox · 1 option");
    expect(browserSummary(null, null)).toBe("");
    expect(browserSummary("webkit", { locale: "de-DE", args: [], headless: null, extra_http_headers: {}, record_video: true })).toBe("WebKit · 2 options");
  });
});
