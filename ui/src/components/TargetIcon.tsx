import { BROWSER_CHOICES } from "../lib/browserOptionsForm";
import type { Environment } from "../lib/types";
import { Icon, type IconName } from "./Icon";

const BROWSER_ICONS: Record<string, IconName> = { "": "chrome", chromium: "chrome", chrome: "chrome", msedge: "edge", firefox: "firefox", webkit: "compass" };

/** What an environment drives: a native app (iOS, Android) or a web app in one browser. */
export function TargetIcon({ env, size = 16 }: { env: Environment; size?: number }) {
  const browser = env.browser ?? "";
  const kind = env.mobile ? (env.mobile.platform === "ios" ? "ios" : "android") : "web";
  const browserName = BROWSER_CHOICES.find((choice) => choice.value === browser)?.label ?? browser;
  const label = kind === "ios" ? "Native app (iOS)" : kind === "android" ? "Native app (Android)" : `Web app in ${browserName}: ${env.base_url ?? ""}`;
  const icon: IconName = kind === "ios" ? "apple" : kind === "android" ? "android" : (BROWSER_ICONS[browser] ?? "envs");
  return (
    <span className={`env-target env-target-${kind}`} title={label}>
      <Icon name={icon} size={size} label={label} />
    </span>
  );
}
