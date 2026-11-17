import { useLayoutEffect, useMemo, useRef, type ChangeEvent, type KeyboardEvent } from "react";
import { gherkinTokens } from "../lib/gherkinTokens";
import { indentEdit, moveLinesEdit, type IndentEdit } from "../lib/indent";
import { useEditHistory } from "../lib/useEditHistory";
import { environmentTrigger, placeholderTrigger, usePlaceholderSuggest, type Suggestion } from "./PlaceholderSuggest";

/** A plain textarea with a line-number gutter; `errorLine` marks the line a validation error names. */
export function GherkinEditor({
  value,
  onChange,
  errorLine = null,
  label = "Gherkin",
  rows = 18,
  id = "gherkin-editor",
  describedBy,
  suggestions = [],
  environments = [],
}: {
  value: string;
  onChange: (value: string) => void;
  errorLine?: number | null;
  label?: string;
  rows?: number;
  id?: string;
  describedBy?: string;
  suggestions?: Suggestion[];
  environments?: Suggestion[];
}) {
  const gutter = useRef<HTMLDivElement>(null);
  const { ref, inputProps, menu, check } = usePlaceholderSuggest<HTMLTextAreaElement>([placeholderTrigger(suggestions), environmentTrigger(environments)], onChange);
  const highlight = useRef<HTMLPreElement>(null);
  const kinds = useMemo(() => new Map(suggestions.flatMap((item) => (item.kind ? [[item.name, item.kind] as const] : []))), [suggestions]);
  const tokens = useMemo(() => gherkinTokens(value, kinds), [value, kinds]);
  const leaving = useRef(false);
  const selection = useRef<[number, number] | null>(null);
  useLayoutEffect(() => {
    if (ref.current && selection.current) {
      ref.current.setSelectionRange(...selection.current);
      selection.current = null;
    }
  });
  const history = useEditHistory(value, onChange, ref, (range) => {
    selection.current = range;
  });
  const programmatic = useRef(false);
  const apply = (field: HTMLTextAreaElement, edit: IndentEdit | null) => {
    if (!edit) return;
    field.setSelectionRange(edit.from, edit.to);
    programmatic.current = true;
    const typed = typeof document.execCommand === "function" && document.execCommand("insertText", false, edit.text);
    programmatic.current = false;
    if (typed) field.setSelectionRange(edit.selectionStart, edit.selectionEnd);
    else {
      selection.current = [edit.selectionStart, edit.selectionEnd];
      onChange(field.value.slice(0, edit.from) + edit.text + field.value.slice(edit.to));
    }
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const field = event.currentTarget;
    const key = event.key.toLowerCase();
    if ((event.metaKey || event.ctrlKey) && !event.altKey && (key === "z" || key === "y")) {
      event.preventDefault();
      if (key === "y" || event.shiftKey) history.redo();
      else history.undo();
      return;
    }
    if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
      event.preventDefault();
      leaving.current = false;
      apply(field, moveLinesEdit(field.value, field.selectionStart, field.selectionEnd, event.key === "ArrowUp" ? "up" : "down"));
      return;
    }
    (inputProps.onKeyDown as (event: KeyboardEvent<HTMLTextAreaElement>) => void)(event);
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      leaving.current = true;
      return;
    }
    const leave = leaving.current;
    leaving.current = false;
    if (event.key !== "Tab" || leave || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    apply(field, indentEdit(field.value, field.selectionStart, field.selectionEnd, event.shiftKey));
  };
  const count = useMemo(() => Math.max(value.split("\n").length, rows), [value, rows]);
  return (
    <div className="placeholder-host">
      <div className="code-editor">
        <label htmlFor={id} className="visually-hidden">
          {label}
        </label>
        <span id={`${id}-keys`} className="visually-hidden">
          Tab indents and Shift+Tab outdents; Alt+Up and Alt+Down (Option on a Mac) move the line up or down; Ctrl+Z undoes and Ctrl+Y redoes (Command+Z and Command+Shift+Z on a Mac). Press Escape,
          then Tab, to leave the editor.
        </span>
        <div className="code-gutter" ref={gutter} aria-hidden="true">
          {Array.from({ length: count }, (_, offset) => (
            <span key={offset} className={offset + 1 === errorLine ? "gutter-error" : undefined}>
              {offset + 1}
            </span>
          ))}
        </div>
        <div className="code-surface">
          <pre className="code-highlight" ref={highlight} aria-hidden="true">
            {tokens.map((token, index) =>
              token.kind ? (
                <span key={index} className={`tok tok-${token.kind}`}>
                  {token.text}
                </span>
              ) : (
                token.text
              ),
            )}
            {"\n "}
          </pre>
          <textarea
            {...inputProps}
            onKeyDown={onKeyDown}
            ref={ref}
            id={id}
            className="code-input"
            value={value}
            rows={rows}
            spellCheck={false}
            autoCapitalize="off"
            autoComplete="off"
            wrap="off"
            aria-invalid={errorLine !== null ? true : undefined}
            aria-describedby={[`${id}-keys`, describedBy].filter(Boolean).join(" ")}
            onSelect={history.remember}
            onChange={(event: ChangeEvent<HTMLTextAreaElement>) => {
              if (!programmatic.current) history.typed();
              onChange(event.target.value);
              check(event.target);
            }}
            onScroll={(event) => {
              if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop;
              if (highlight.current) {
                highlight.current.scrollTop = event.currentTarget.scrollTop;
                highlight.current.scrollLeft = event.currentTarget.scrollLeft;
              }
            }}
          />
        </div>
      </div>
      {menu}
    </div>
  );
}
