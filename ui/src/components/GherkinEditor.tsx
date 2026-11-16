import { useMemo, useRef, type ChangeEvent } from "react";

/** A plain textarea with a line-number gutter; `errorLine` marks the line a validation error names. */
export function GherkinEditor({
  value,
  onChange,
  errorLine = null,
  label = "Gherkin",
  rows = 18,
  id = "gherkin-editor",
  describedBy,
}: {
  value: string;
  onChange: (value: string) => void;
  errorLine?: number | null;
  label?: string;
  rows?: number;
  id?: string;
  describedBy?: string;
}) {
  const gutter = useRef<HTMLDivElement>(null);
  const count = useMemo(() => Math.max(value.split("\n").length, rows), [value, rows]);
  return (
    <div className="code-editor">
      <label htmlFor={id} className="visually-hidden">
        {label}
      </label>
      <div className="code-gutter" ref={gutter} aria-hidden="true">
        {Array.from({ length: count }, (_, offset) => (
          <span key={offset} className={offset + 1 === errorLine ? "gutter-error" : undefined}>
            {offset + 1}
          </span>
        ))}
      </div>
      <textarea
        id={id}
        className="code-input"
        value={value}
        rows={rows}
        spellCheck={false}
        autoCapitalize="off"
        autoComplete="off"
        wrap="off"
        aria-invalid={errorLine !== null ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event: ChangeEvent<HTMLTextAreaElement>) => onChange(event.target.value)}
        onScroll={(event) => {
          if (gutter.current) gutter.current.scrollTop = event.currentTarget.scrollTop;
        }}
      />
    </div>
  );
}
