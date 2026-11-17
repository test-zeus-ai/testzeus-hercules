import { useId, useLayoutEffect, useRef, useState, type InputHTMLAttributes, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { BUILTIN_PLACEHOLDERS, DOWNLOAD_PLACEHOLDERS, type FormDatum } from "../lib/caseForm";
import { environmentVariables } from "../lib/environmentForm";
import type { Environment } from "../lib/types";

export type SuggestionKind = "data" | "env" | "builtin";

export interface Suggestion {
  name: string;
  detail: string;
  kind?: SuggestionKind;
}

type Field = HTMLInputElement | HTMLTextAreaElement;

export interface Trigger {
  opening: RegExp;
  suggestions: Suggestion[];
  text: (name: string) => string;
  closing: RegExp;
  label: string;
}

export function placeholderTrigger(suggestions: Suggestion[]): Trigger {
  return { opening: /(?<token>\{\{\s*(?<query>[A-Za-z0-9_]*))$/, suggestions, text: (name) => `{{${name}}}`, closing: /^\s*\}\}/, label: "Placeholders" };
}

export function environmentTrigger(suggestions: Suggestion[]): Trigger {
  return { opening: /^[ \t]*(?:@\S+[ \t]+)*(?<token>@env:(?<query>[A-Za-z0-9_-]*))$/, suggestions, text: (name) => `@env:${name}`, closing: /^[A-Za-z0-9_-]*/, label: "Environments" };
}
const MAX_SHOWN = 8;

export function placeholderSuggestions(env: Pick<Environment, "name" | "secret_names" | "variables"> | undefined, testData: FormDatum[]): Suggestion[] {
  const found = new Map<string, Suggestion>();
  for (const row of testData) {
    const name = row.key.trim();
    if (name && !found.has(name)) found.set(name, { name, detail: row.secret ? `test data · secret ${row.value.trim()}` : "test data", kind: "data" });
  }
  for (const row of env ? environmentVariables(env) : []) {
    if (!found.has(row.key)) found.set(row.key, { name: row.key, detail: row.secret ? `${env?.name} · secret ${row.value}` : `${env?.name} · ${row.value}`, kind: "env" });
  }
  for (const name of BUILTIN_PLACEHOLDERS) found.set(name, { name, detail: "built-in", kind: "builtin" });
  for (const name of DOWNLOAD_PLACEHOLDERS) found.set(name, { name, detail: "after a download step", kind: "builtin" });
  return [...found.values()];
}

export function matchingSuggestions(suggestions: Suggestion[], query: string): Suggestion[] {
  const wanted = query.toLowerCase();
  const starts = suggestions.filter((item) => item.name.toLowerCase().startsWith(wanted));
  const contains = suggestions.filter((item) => !item.name.toLowerCase().startsWith(wanted) && item.name.toLowerCase().includes(wanted));
  return [...starts, ...contains].slice(0, MAX_SHOWN);
}

let ruler: HTMLSpanElement | null = null;

function textWidth(text: string, style: CSSStyleDeclaration): number {
  if (!ruler) {
    ruler = document.createElement("span");
    ruler.setAttribute("aria-hidden", "true");
    Object.assign(ruler.style, { position: "absolute", visibility: "hidden", whiteSpace: "pre", left: "-9999px", top: "0" });
    document.body.appendChild(ruler);
  }
  ruler.style.font = style.font || `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  ruler.style.letterSpacing = style.letterSpacing;
  ruler.textContent = text.replace(/\t/g, "  ");
  return ruler.getBoundingClientRect().width;
}

function caretPoint(field: Field, index: number): { top: number; left: number } {
  const style = getComputedStyle(field);
  const lines = field.value.slice(0, index).split("\n");
  const padTop = parseFloat(style.paddingTop) || 0;
  const padLeft = parseFloat(style.paddingLeft) || 0;
  const lineHeight = parseFloat(style.lineHeight) || 20;
  const box = field.getBoundingClientRect();
  const origin = (field.closest(".placeholder-host") ?? field.parentElement ?? document.body).getBoundingClientRect();
  const fieldTop = box.top - origin.top;
  const fieldLeft = box.left - origin.left;
  const top = field instanceof HTMLTextAreaElement ? fieldTop + padTop + lines.length * lineHeight - field.scrollTop + 2 : fieldTop + box.height + 2;
  const left = fieldLeft + padLeft + textWidth(lines[lines.length - 1], style) - field.scrollLeft;
  return { top, left: Math.max(fieldLeft, Math.min(left, fieldLeft + Math.max(field.clientWidth - 240, 0))) };
}

interface Open {
  trigger: Trigger;
  start: number;
  caret: number;
  query: string;
  top: number;
  left: number;
}

export function usePlaceholderSuggest<T extends Field>(
  triggers: Trigger[],
  onChange: (value: string) => void,
): { ref: RefObject<T | null>; inputProps: Record<string, unknown>; menu: ReactNode; check: (field: T) => void } {
  const ref = useRef<T | null>(null);
  const id = useId();
  const [open, setOpen] = useState<Open | null>(null);
  const [active, setActive] = useState(0);
  const caretAfter = useRef<number | null>(null);
  const shown = open ? matchingSuggestions(open.trigger.suggestions, open.query) : [];

  useLayoutEffect(() => {
    const field = ref.current;
    if (field && caretAfter.current !== null) {
      field.focus();
      field.setSelectionRange(caretAfter.current, caretAfter.current);
      caretAfter.current = null;
    }
  });

  const check = (field: T) => {
    const caret = field.selectionStart ?? field.value.length;
    if (field.selectionEnd !== null && field.selectionEnd !== caret) return setOpen(null);
    const before = field.value.slice(field.value.lastIndexOf("\n", caret - 1) + 1, caret);
    for (const trigger of triggers) {
      const groups = trigger.opening.exec(before)?.groups;
      if (!groups) continue;
      const start = caret - groups.token.length;
      setOpen({ trigger, start, caret, query: groups.query, ...caretPoint(field, start) });
      setActive(0);
      return;
    }
    setOpen(null);
  };

  const insert = (name: string) => {
    const field = ref.current;
    if (!field || !open) return;
    const rest = field.value.slice(open.caret).replace(open.trigger.closing, "");
    const text = open.trigger.text(name);
    caretAfter.current = open.start + text.length;
    onChange(field.value.slice(0, open.start) + text + rest);
    setOpen(null);
  };

  const onKeyDown = (event: KeyboardEvent<T>) => {
    if (!open || shown.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current + step + shown.length) % shown.length);
    } else if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      insert(shown[Math.min(active, shown.length - 1)].name);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(null);
    }
  };

  const expanded = Boolean(open && shown.length);
  const inputProps = {
    onKeyDown,
    onKeyUp: (event: KeyboardEvent<T>) => {
      if (!["ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"].includes(event.key)) check(event.currentTarget);
    },
    onClick: (event: { currentTarget: T }) => check(event.currentTarget),
    onBlur: () => setOpen(null),
    "aria-autocomplete": "list",
    "aria-expanded": expanded,
    "aria-controls": expanded ? `${id}-menu` : undefined,
    "aria-activedescendant": expanded ? `${id}-option-${Math.min(active, shown.length - 1)}` : undefined,
  };

  const menu = expanded ? (
    <ul id={`${id}-menu`} role="listbox" aria-label={open!.trigger.label} className="placeholder-menu" style={{ top: open!.top, left: open!.left }}>
      {shown.map((item, index) => (
        <li
          key={item.name}
          id={`${id}-option-${index}`}
          role="option"
          aria-selected={index === active}
          className={index === active ? "placeholder-option placeholder-option-on" : "placeholder-option"}
          onMouseDown={(event) => {
            event.preventDefault();
            insert(item.name);
          }}
          onMouseEnter={() => setActive(index)}
        >
          <span className="mono">{open!.trigger.text(item.name)}</span>
          <span className="placeholder-detail">{item.detail}</span>
        </li>
      ))}
    </ul>
  ) : null;

  return { ref, inputProps, menu, check };
}

export function SuggestInput({
  suggestions,
  value,
  onChange,
  ...rest
}: { suggestions: Suggestion[]; value: string; onChange: (value: string) => void } & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const { ref, inputProps, menu, check } = usePlaceholderSuggest<HTMLInputElement>([placeholderTrigger(suggestions)], onChange);
  return (
    <span className="placeholder-host">
      <input
        {...rest}
        {...inputProps}
        ref={ref}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          check(event.target);
        }}
      />
      {menu}
    </span>
  );
}
