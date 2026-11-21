import type { IconName } from "../components/Icon";
import type { CloudDesktopBrowser, CloudDevices, CloudMobileDevice } from "./types";

export interface MobileFilter {
  query: string;
  platform: "" | "android" | "ios";
  kind: "" | "phone" | "tablet";
  /** "" both, "real" real devices only, "virtual" emulators and simulators only. */
  real: "" | "real" | "virtual";
}

export interface DesktopFilter {
  query: string;
  os: string;
}

export const EMPTY_MOBILE_FILTER: MobileFilter = { query: "", platform: "", kind: "", real: "" };
export const EMPTY_DESKTOP_FILTER: DesktopFilter = { query: "", os: "" };

const BROWSER_LABELS: Record<string, string> = { chrome: "Chrome", chromefortesting: "Chrome for Testing", edge: "Edge", firefox: "Firefox", safari: "Safari", ie: "Internet Explorer", opera: "Opera" };
const BROWSER_ICONS: Record<string, IconName> = { chrome: "chrome", chromefortesting: "chrome", edge: "edge", firefox: "firefox", safari: "compass" };

function matches(text: string, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const haystack = text.toLowerCase();
  return words.every((word) => haystack.includes(word));
}

export function platformLabel(platform: CloudMobileDevice["platform"]): string {
  return platform === "ios" ? "iOS" : "Android";
}

export function browserLabel(browser: string): string {
  return BROWSER_LABELS[browser] ?? (browser ? browser[0].toUpperCase() + browser.slice(1) : browser);
}

export function browserIcon(browser: string): IconName {
  return BROWSER_ICONS[browser] ?? "envs";
}

export function osIcon(os: string): IconName {
  return os === "Windows" ? "windows" : os === "macOS" ? "apple" : "envs";
}

export function filterMobile(devices: CloudMobileDevice[], filter: MobileFilter): CloudMobileDevice[] {
  return devices.filter(
    (device) =>
      (!filter.platform || device.platform === filter.platform) &&
      (!filter.kind || device.kind === filter.kind) &&
      (!filter.real || device.real === (filter.real === "real")) &&
      matches(`${device.name} ${platformLabel(device.platform)} ${device.os_version}`, filter.query),
  );
}

export function filterDesktop(browsers: CloudDesktopBrowser[], filter: DesktopFilter): CloudDesktopBrowser[] {
  return browsers.filter((item) => (!filter.os || item.os === filter.os) && matches(`${item.os} ${item.os_version} ${item.browser} ${browserLabel(item.browser)} ${item.versions.join(" ")}`, filter.query));
}

/** The OS families in the list, in their order. */
export function desktopOses(browsers: CloudDesktopBrowser[]): string[] {
  return [...new Set(browsers.map((item) => item.os))];
}

/** "131, 130, 129 … 90" for a long list of versions (newest first); all of them when there are few. */
export function versionsSummary(versions: string[]): string {
  return versions.length <= 4 ? versions.join(", ") : `${versions.slice(0, 3).join(", ")} … ${versions[versions.length - 1]}`;
}

/** Whether the list has both real devices and emulators or simulators (so filtering by it means something). */
export function mixesRealAndVirtual(devices: CloudMobileDevice[]): boolean {
  return devices.some((device) => device.real) && devices.some((device) => !device.real);
}

export function devicesSummary(catalog: CloudDevices): string {
  const mobile = `${catalog.mobile.length} mobile ${catalog.mobile.length === 1 ? "device" : "devices"}`;
  const desktop = `${catalog.desktop.length} desktop ${catalog.desktop.length === 1 ? "browser" : "browsers"}`;
  return `${mobile} · ${desktop}`;
}
