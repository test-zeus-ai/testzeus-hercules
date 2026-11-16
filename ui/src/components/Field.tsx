import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";

/**
 * A labelled form control. The label is tied to the control by id (not by wrapping), so a
 * select's option text never leaks into its accessible name; help or error text is linked
 * with aria-describedby.
 */
export function Field({ id, label, error, help, children }: { id: string; label: string; error?: string; help?: ReactNode; children: ReactElement<Record<string, unknown>> }) {
  const describedBy = error ? `${id}-error` : help ? `${id}-help` : undefined;
  const control = Children.only(children);
  const wired = isValidElement(control) ? cloneElement(control, { id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined }) : control;
  return (
    <div className={`field${error ? " field-invalid" : ""}`}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {wired}
      {error ? (
        <span className="field-error" id={`${id}-error`} role="alert">
          {error}
        </span>
      ) : help ? (
        <span className="field-help" id={`${id}-help`}>
          {help}
        </span>
      ) : null}
    </div>
  );
}
