import { useEffect, useState } from "react";

export type FontChoice = "manrope" | "inter" | "atkinson" | "system";
const KEY = "hercules.font";
const CHOICES: FontChoice[] = ["manrope", "inter", "atkinson", "system"];

function stored(): FontChoice {
  try {
    const value = window.localStorage.getItem(KEY);
    return CHOICES.includes(value as FontChoice) ? (value as FontChoice) : "manrope";
  } catch {
    return "manrope";
  }
}

export function applyFont(choice: FontChoice): void {
  const root = document.documentElement;
  if (choice === "manrope") delete root.dataset.font;
  else root.dataset.font = choice;
}

/** The UI's text font; remembered per browser. */
export function useFont(): [FontChoice, (choice: FontChoice) => void] {
  const [choice, setChoice] = useState<FontChoice>(stored);
  useEffect(() => {
    applyFont(choice);
    try {
      if (choice === "manrope") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, choice);
    } catch {
      /* storage unavailable: the choice lasts for this page only */
    }
  }, [choice]);
  return [choice, setChoice];
}
