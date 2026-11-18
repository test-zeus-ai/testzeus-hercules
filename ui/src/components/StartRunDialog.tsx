import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent, type SelectHTMLAttributes } from "react";
import { api, post } from "../lib/api";
import { isCaseList, isModelsReport, isRunCase } from "../lib/guards";
import { BASE_URL_PATTERN, environmentTag, errorLine } from "../lib/caseForm";
import { targetSummary } from "../lib/environmentForm";
import { formFromSettings, rerunOptionsBody, runOptionsBody, validateRunOptions, type RunOptions } from "../lib/runOptions";
import { folderOptions, within } from "../lib/caseTree";
import type { CaseListing, Environment, ModelsReport, RerunScope, RunView, TestCase } from "../lib/types";
import { AnimatedHeight } from "./AnimatedHeight";
import { Field } from "./Field";
import { GherkinEditor } from "./GherkinEditor";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { OptionGroup, RunOptionsFields } from "./RunOptionsFields";
import { TargetIcon } from "./TargetIcon";

type Source = "saved" | "folder" | "gherkin" | "description";

const SAMPLE = `Feature: Sign in
  Scenario: Registered user signs in
    Given I open "/login"
    When I fill "user@example.com" in "Email"
    And I click the "Sign in" button
    Then I should see "Welcome"
`;

export interface StartRunInitial {
  source?: Source;
  casePath?: string;
  folder?: string;
  gherkin?: string;
  testData?: Record<string, string | { secret: string }>;
}

/** Re-run this finished run instead of starting a new case: the fields start from its options. */
export interface RerunOf {
  run: RunView;
  scope: RerunScope;
}

interface BatchCreated {
  batch: string;
  folder: string;
  runs: { run_id: string }[];
  skipped: { name: string; reason: string }[];
}

/**
 * A select whose field shows the chosen environment's name followed by its target icon (a browser or a
 * native app); "None" shows as a plain select. The name and icon are drawn over the select, whose own
 * text is hidden, so clicks, keys and the option list stay the select's.
 */
function EnvironmentSelect({ selected, ...select }: { selected?: Environment } & SelectHTMLAttributes<HTMLSelectElement>) {
  const name = selected?.name ?? (select.value ? `${String(select.value)} (not saved)` : "");
  return (
    <span className={`select-with-icon${name ? " has-display" : ""}`}>
      <select {...select} />
      {name ? (
        <span className="select-display" aria-hidden="true">
          <span className="select-display-text">{name}</span>
          {selected ? (
            <TargetIcon env={selected} />
          ) : (
            <span className="env-target">
              <Icon name="envs" size={16} />
            </span>
          )}
        </span>
      ) : null}
    </span>
  );
}

export function StartRunDialog({ onClose, onStarted, initial = {}, rerun }: { onClose: () => void; onStarted: (runId: string, batch?: string) => void; initial?: StartRunInitial; rerun?: RerunOf }) {
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api<CaseListing>("/api/cases", { guard: isCaseList }), enabled: !rerun });
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => api<{ environments: Environment[] }>("/api/environments") });
  const models = useQuery({ queryKey: ["models", false], queryFn: () => api<ModelsReport>("/api/models", { guard: isModelsReport }) });
  const [source, setSource] = useState<Source>(initial.source ?? "saved");
  const [casePath, setCasePath] = useState(initial.casePath ?? "");
  const [folder, setFolder] = useState(initial.folder ?? "");
  const [subfolders, setSubfolders] = useState(true);
  const [gherkin, setGherkin] = useState(initial.gherkin ?? SAMPLE);
  const [description, setDescription] = useState("");
  const [initialForm] = useState(() => formFromSettings(rerun?.run.settings));
  const [chosenEnvironment, setChosenEnvironment] = useState<string | null>(initialForm.environment || null);
  const [baseUrl, setBaseUrl] = useState(initialForm.baseUrl);
  const [mode, setMode] = useState(initialForm.mode);
  const [egress, setEgress] = useState(initialForm.egress);
  const [headed, setHeaded] = useState(initialForm.headed);
  const [options, setOptions] = useState<RunOptions>(initialForm.options);
  const [localError, setLocalError] = useState<string | null>(null);

  const validCases = (cases.data?.cases ?? []).filter((entry) => entry.valid);
  const chosenCase = casePath || validCases[0]?.path || "";
  const folderCases = (cases.data?.cases ?? []).filter((entry) => (subfolders ? within(entry.folder ?? "", folder) : (entry.folder ?? "") === folder));
  const folderReady = folderCases.filter((entry) => entry.valid && entry.status !== "draft").length;
  const folderSkipped = folderCases.length - folderReady;
  const rerunCase = useQuery({
    queryKey: ["run-case", rerun?.run.run_id],
    queryFn: () => api<{ test_case: TestCase }>(`/api/runs/${encodeURIComponent(rerun?.run.run_id ?? "")}/case`, { guard: isRunCase }),
    enabled: Boolean(rerun),
    staleTime: Infinity,
  });
  const caseEnvironment = rerun
    ? (rerunCase.data?.test_case.environment?.ref ?? null)
    : source === "saved"
      ? (validCases.find((entry) => entry.path === chosenCase)?.environment ?? null)
      : source === "gherkin"
        ? environmentTag(gherkin)
        : null;
  const environment = chosenEnvironment ?? caseEnvironment ?? "";
  const requestedEnvironment = environment !== caseEnvironment ? environment : rerun ? initialForm.environment : "";
  const start = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<{ run_id: string }>(rerun ? `/api/runs/${encodeURIComponent(rerun.run.run_id)}/rerun` : "/api/runs", body),
    onSuccess: (created) => onStarted(created.run_id),
  });
  const startBatch = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<BatchCreated>("/api/runs/batch", body),
    onSuccess: (created) => {
      if (created.runs.length) onStarted(created.runs[0].run_id, created.batch);
      else setLocalError(`Nothing ran: ${created.skipped.map((skip) => `${skip.name} (${skip.reason})`).join("; ")}`);
    },
  });

  const optionErrors = validateRunOptions(options);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setLocalError(null);
    const invalid = optionErrors.destructiveTerms ? "start-destructive-terms" : optionErrors.approvalTimeout ? "start-approval-timeout" : null;
    if (invalid) {
      document.getElementById(invalid)?.focus();
      return;
    }
    if (baseUrl.trim() && !BASE_URL_PATTERN.test(baseUrl.trim())) return setLocalError("The base URL must be an http(s) URL with a host.");
    if (rerun) {
      start.mutate({ scope: rerun.scope, ...rerunOptionsBody({ environment: requestedEnvironment, baseUrl, mode, egress, headed, options }) });
      return;
    }
    const body: Record<string, unknown> = { mode };
    if (source === "folder") {
      if (!folderReady) return setLocalError(folderCases.length ? "Every case in this folder is a draft or does not load." : "This folder has no cases.");
      if (baseUrl.trim()) body.base_url = baseUrl.trim();
      if (requestedEnvironment) body.environment = requestedEnvironment;
      if (egress) body.egress = egress;
      if (headed) body.headed = true;
      startBatch.mutate({ ...body, folder, subfolders, ...runOptionsBody(options) });
      return;
    }
    if (source === "saved") {
      if (!chosenCase) return setLocalError("Pick a saved case, or paste Gherkin instead.");
      body.case_path = chosenCase;
    } else if (source === "gherkin") {
      if (!gherkin.trim()) return setLocalError("Paste a Gherkin scenario.");
      body.gherkin = gherkin;
      if (initial.testData && Object.keys(initial.testData).length) body.test_data = initial.testData;
    } else {
      if (!description.trim()) return setLocalError("Describe the steps, one per line.");
      body.description = description;
    }
    if (baseUrl.trim()) body.base_url = baseUrl.trim();
    if (requestedEnvironment) body.environment = requestedEnvironment;
    if (egress) body.egress = egress;
    if (headed) body.headed = true;
    start.mutate({ ...body, ...runOptionsBody(options) });
  };

  const hybrid = models.data?.modes.hybrid;
  const error = localError ?? (start.error as Error | null)?.message ?? (startBatch.error as Error | null)?.message ?? null;
  const savedEnvs = envs.data?.environments ?? [];
  const selectedEnv = savedEnvs.find((env) => env.name === environment);

  return (
    <Modal title={rerun ? (rerun.scope === "failed_only" ? "Re-run if failed" : "Re-run") : "Start a run"} onClose={onClose} wide>
      <form className="form" onSubmit={submit} noValidate>
        {rerun ? (
          <div className="field rerun-of">
            <span className="field-label">What to run</span>
            <p>
              <strong>{rerun.run.title ?? rerun.run.case_id ?? rerun.run.run_id}</strong> again: a re-run of <span className="mono">{rerun.run.run_id}</span>
              {rerun.scope === "failed_only" ? ", only if that run did not pass" : ""}. Saved case files are read again. The options below start as that run's; change any of them first.
            </p>
          </div>
        ) : (
          <>
            <fieldset className="field">
              <legend>What to run</legend>
              <div className="segmented" role="radiogroup" aria-label="Source">
                {(
                  [
                    ["saved", "Saved case"],
                    ["folder", "Folder"],
                    ["gherkin", "Paste Gherkin"],
                    ["description", "Plain language (draft)"],
                  ] as const
                ).map(([id, label]) => (
                  <label key={id} className={`segment${source === id ? " segment-on" : ""}`}>
                    <input
                      type="radio"
                      name="source"
                      value={id}
                      checked={source === id}
                      onChange={() => {
                        setSource(id);
                        setChosenEnvironment(null);
                      }}
                      className="visually-hidden"
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>

            <AnimatedHeight swapKey={source}>
              {source === "saved" ? (
                <Field id="start-case" label="Case" required>
                  <select
                    value={chosenCase}
                    onChange={(event) => {
                      setCasePath(event.target.value);
                      setChosenEnvironment(null);
                    }}
                    disabled={validCases.length === 0}
                  >
                    {validCases.length === 0 ? <option value="">No saved cases yet</option> : null}
                    {validCases.map((entry) => (
                      <option key={entry.path} value={entry.path} title={entry.name}>
                        {entry.title ?? entry.name}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : source === "folder" ? (
                <div className="field-row">
                  <Field
                    id="start-folder"
                    label="Folder"
                    required
                    help={`${folderReady} ${folderReady === 1 ? "case runs" : "cases run"}, one run each${folderSkipped ? `; ${folderSkipped} ${folderSkipped === 1 ? "draft or broken case is" : "drafts or broken cases are"} skipped` : ""}.`}
                  >
                    <select value={folder} onChange={(event) => setFolder(event.target.value)}>
                      <option value="">All cases</option>
                      {folderOptions(cases.data?.folders ?? []).map((option) => (
                        <option key={option.path} value={option.path}>
                          {option.path}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <label className="field field-check field-check-inline">
                    <input type="checkbox" checked={subfolders} onChange={(event) => setSubfolders(event.target.checked)} />
                    <span>Include subfolders</span>
                  </label>
                </div>
              ) : source === "gherkin" ? (
                <div className="field">
                  <span className="field-label is-required">Scenario</span>
                  <GherkinEditor value={gherkin} onChange={setGherkin} rows={10} id="start-gherkin" errorLine={errorLine(start.error?.message)} />
                </div>
              ) : (
                <Field id="start-description" label="Steps, one per line" required help="Becomes a draft: review the steps on the run page, then approve it.">
                  <textarea rows={6} value={description} onChange={(event) => setDescription(event.target.value)} placeholder={"Open /login\nSign in as the demo user\nCheck the welcome message"} />
                </Field>
              )}
            </AnimatedHeight>
          </>
        )}

        <div className="field-row">
          <Field
            id="start-environment"
            label="Environment"
            help={
              !rerun && source === "folder"
                ? "Each case runs in its own environment; pick one to run them all there."
                : caseEnvironment
                  ? `The case runs in ${caseEnvironment}; pick another to run it there instead.`
                  : undefined
            }
          >
            <EnvironmentSelect selected={selectedEnv} value={environment} onChange={(event) => setChosenEnvironment(event.target.value)}>
              <option value="" disabled={Boolean(caseEnvironment)}>
                {!rerun && source === "folder" ? "Each case's own" : "None"}
              </option>
              {environment && envs.data && !selectedEnv ? <option value={environment}>{environment} (not saved)</option> : null}
              {savedEnvs.map((env) => (
                <option key={env.name} value={env.name} title={targetSummary(env)}>
                  {env.name}
                </option>
              ))}
            </EnvironmentSelect>
          </Field>
          <Field id="start-base-url" label="Base URL">
            <input type="url" inputMode="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://localhost:3000 (optional)" />
          </Field>
        </div>

        <div className="field-row">
          <Field id="start-mode" label="Mode">
            <select value={mode} onChange={(event) => setMode(event.target.value)}>
              <option value="auto">Auto</option>
              <option value="pure">Pure (local model only)</option>
              <option value="hybrid" disabled={hybrid ? !hybrid.key_set : false}>
                Hybrid (JEV decides)
                {hybrid && !hybrid.key_set ? " — key not set" : ""}
              </option>
            </select>
          </Field>
          <Field id="start-egress" label="Egress">
            <select value={egress} onChange={(event) => setEgress(event.target.value)}>
              <option value="">Project default ({models.data?.egress_policy ?? "local"})</option>
              <option value="local">local: nothing leaves this machine</option>
              <option value="hybrid-text">hybrid-text: page text to JEV</option>
              <option value="hybrid">hybrid: text, screenshots when unsure</option>
            </select>
          </Field>
        </div>

        <RunOptionsFields value={options} errors={optionErrors} onChange={setOptions} />

        <OptionGroup title="Advanced" summary={headed ? "browser window shown" : null}>
          <label className="field field-check">
            <input type="checkbox" checked={headed} onChange={(event) => setHeaded(event.target.checked)} />
            <span>Show the browser window</span>
          </label>
        </OptionGroup>

        {error ? (
          <p className="callout callout-fail" role="alert">
            {error}
          </p>
        ) : null}
        <footer className="form-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="button button-primary" disabled={start.isPending || startBatch.isPending}>
            {start.isPending || startBatch.isPending ? "Starting…" : rerun ? "Start re-run" : source === "folder" ? `Run ${folderReady} ${folderReady === 1 ? "case" : "cases"}` : "Start run"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
