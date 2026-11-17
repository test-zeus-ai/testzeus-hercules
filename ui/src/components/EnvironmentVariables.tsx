import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type KeyboardEvent } from "react";
import { api, put, remove } from "../lib/api";
import type { FormDatum } from "../lib/caseForm";
import { secretVariables } from "../lib/environmentForm";
import type { SecretSource, SecretStatus } from "../lib/types";
import { Icon } from "./Icon";

const SOURCE_LABEL: Record<SecretSource, string> = {
  env: "set in the daemon's environment",
  file: "saved in Hercules",
  none: "not set",
};

type Outcome = { tone: "ok" | "fail" | "note"; text: string };

function SecretValueForm({ name, source }: { name: string; source: SecretSource }) {
  const client = useQueryClient();
  const [value, setValue] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const refresh = () => void client.invalidateQueries({ queryKey: ["secrets"] });
  const save = useMutation({
    mutationFn: () => put<SecretStatus & { note: string | null }>(`/api/secrets/${name}`, { value }),
    onSuccess: (saved) => {
      setValue("");
      setOutcome(saved.note ? { tone: "note", text: saved.note } : { tone: "ok", text: `Saved ${name}.` });
      refresh();
    },
    onError: (error) => setOutcome({ tone: "fail", text: (error as Error).message }),
  });
  const forget = useMutation({
    mutationFn: () => remove<SecretStatus & { removed: boolean }>(`/api/secrets/${name}`),
    onSuccess: () => {
      setOutcome({ tone: "note", text: `Removed the saved value of ${name}.` });
      refresh();
    },
    onError: (error) => setOutcome({ tone: "fail", text: (error as Error).message }),
  });
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (value) save.mutate();
  };
  return (
    <div className="secret-value" role="group" aria-label={`Save a value for ${name}`}>
      <label className="visually-hidden" htmlFor={`secret-${name}`}>
        Value of {name}
      </label>
      <input id={`secret-${name}`} type="password" value={value} onChange={(event) => setValue(event.target.value)} onKeyDown={onKeyDown} autoComplete="off" spellCheck={false} placeholder={source === "file" ? "Replace the saved value" : "Paste the value"} />
      <button type="button" className="button button-small" onClick={() => save.mutate()} disabled={!value || save.isPending}>
        Save value
      </button>
      {source === "file" ? (
        <button type="button" className="button button-small button-ghost" onClick={() => forget.mutate()} disabled={forget.isPending}>
          Remove saved value
        </button>
      ) : null}
      {outcome ? (
        <span className={outcome.tone === "fail" ? "field-error" : "field-help"} role={outcome.tone === "fail" ? "alert" : "status"}>
          {outcome.text}
        </span>
      ) : null}
    </div>
  );
}

export function EnvironmentVariables({ rows, errors, onChange }: { rows: FormDatum[]; errors: Record<string, string>; onChange: (rows: FormDatum[]) => void }) {
  const secrets = secretVariables(rows);
  const status = useQuery({
    queryKey: ["secrets", secrets.join(",")],
    queryFn: () => api<{ secrets: SecretStatus[]; file: string }>(`/api/secrets?names=${encodeURIComponent(secrets.join(","))}`),
    enabled: secrets.length > 0,
  });
  const sources = new Map((status.data?.secrets ?? []).map((item) => [item.name, item.source]));
  const setRow = (offset: number, change: Partial<FormDatum>) => onChange(rows.map((row, index) => (index === offset ? { ...row, ...change } : row)));
  return (
    <fieldset className="data-editor env-variables">
      <legend>Variables and secrets</legend>
      <p className="field-help">
        Every case and .feature run with this environment can use these as {"{{name}}"}, also in the base URL. A secret names an environment variable: its value is read at run time from the daemon's environment or from values saved here ({status.data?.file ?? "~/.hercules/secrets.env"}), and is never stored in the environment or shown again.
      </p>
      {rows.map((row, offset) => {
        const keyError = errors[`variables.${offset}.key`];
        const valueError = errors[`variables.${offset}.value`];
        const variable = row.value.trim();
        const source = row.secret ? sources.get(variable) : undefined;
        return (
          <div key={offset} className="data-row-group">
            <div className="data-row">
              <label className="visually-hidden" htmlFor={`env-var-${offset}-key`}>
                Variable name
              </label>
              <input id={`env-var-${offset}-key`} className="mono" value={row.key} placeholder="name" onChange={(event) => setRow(offset, { key: event.target.value })} aria-invalid={Boolean(keyError)} />
              <label className="visually-hidden" htmlFor={`env-var-${offset}-value`}>
                {row.secret ? "Environment variable" : "Value"}
              </label>
              <input
                id={`env-var-${offset}-value`}
                className={row.secret ? "mono" : undefined}
                value={row.value}
                placeholder={row.secret ? "ENV_VAR_NAME" : "value"}
                onChange={(event) => setRow(offset, { value: event.target.value })}
                aria-invalid={Boolean(valueError)}
              />
              <label className="field-check">
                <input type="checkbox" checked={row.secret} onChange={(event) => setRow(offset, { secret: event.target.checked })} /> Secret
              </label>
              <button type="button" className="icon-button" onClick={() => onChange(rows.filter((_, index) => index !== offset))} aria-label={`Remove ${row.key || "variable"}`}>
                ×
              </button>
              {keyError || valueError ? (
                <span className="field-error data-error" role="alert">
                  {keyError ?? valueError}
                </span>
              ) : null}
            </div>
            {row.secret && source ? (
              <div className="secret-status">
                <span className={`tag mono secret-source secret-source-${source}`}>
                  <Icon name="key" size={11} /> {variable}: {SOURCE_LABEL[source]}
                </span>
                {source !== "env" ? <SecretValueForm name={variable} source={source} /> : null}
              </div>
            ) : null}
          </div>
        );
      })}
      <button type="button" className="button button-small" onClick={() => onChange([...rows, { key: "", value: "", secret: false }])}>
        Add variable
      </button>
    </fieldset>
  );
}
