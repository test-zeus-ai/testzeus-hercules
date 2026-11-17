import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "hercules.theme";
const ORDER: ThemeChoice[] = ["system", "light", "dark"];

function stored(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

/** Light, dark or follow the system; remembered per browser. */
export function useTheme(): [ThemeChoice, () => void, (choice: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(stored);
  useEffect(() => {
    applyTheme(choice);
    try {
      if (choice === "system") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, choice);
    } catch {
      /* storage unavailable: the choice lasts for this page only */
    }
  }, [choice]);
  const cycle = useCallback(() => setChoice((current) => ORDER[(ORDER.indexOf(current) + 1) % ORDER.length]), []);
  return [choice, cycle, setChoice];
}
