import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { Link, useLocation } from "wouter";
import { CaseForm, TestDataEditor } from "../components/CaseForm";
import { GherkinEditor } from "../components/GherkinEditor";
import { Icon } from "../components/Icon";
import { placeholderSuggestions } from "../components/PlaceholderSuggest";
import { CaseFiles } from "../components/StepFiles";
import { StartRunDialog, type StartRunInitial } from "../components/StartRunDialog";
import { ApiError, api, post, remove } from "../lib/api";
import { emptyForm, environmentTag, errorLine, formUnquoted, gherkinUnquoted, quoteFormPlaceholders, quoteGherkinPlaceholders, fromForm, testDataOf, toForm, validTestData, validateCaseForm, validateTestData, withEnvironmentTag, type CaseFormValue, type FormDatum } from "../lib/caseForm";
import { environmentNames, targetSummary } from "../lib/environmentForm";
import { useDebounced } from "../lib/useDebounced";
import { isCaseList, isSavedCase } from "../lib/guards";
import type { CaseCheck, CaseEntry, Environment, SavedCase, TestCase } from "../lib/types";

type Kind = "feature" | "json";
type Tab = "gherkin" | "form" | "json";

const NEW_FEATURE = `@smoke
Feature: Sign in
  Scenario: Registered user signs in
    Given I open "/login"
    When I fill "user@example.com" in "Email"
    And I click the "Sign in" button
    Then I should see "Welcome"
`;

interface Draft {
  name: string | null; // saved file name, null for a new case
  kind: Kind;
  gherkin: string;
  form: CaseFormValue;
  data: FormDatum[];
  base: TestCase | undefined;
  dirty: boolean;
}

// wouter hands route params through decodeURI, which leaves the escapes of reserved characters
// (`/`, `?`, `#`, `&`, …) in place and turns `%25` into a bare `%`. Decode only those leftovers:
// decodeURIComponent on top would decode twice and throws on a lone `%`.
const RESERVED_ESCAPE = /%(23|24|26|2B|2C|2F|3A|3B|3D|3F|40)/gi;

/** The case file name of a `/cases/:name` route param. */
export function caseNameFromParam(param: string | undefined): string | null {
  if (!param) return null;
  return param.replace(RESERVED_ESCAPE, (_match, hex: string) => String.fromCharCode(Number.parseInt(hex, 16)));
}

export const DISCARD_PROMPT = "Discard the unsaved changes to this case?";

export const deleteCasePrompt = (name: string) => `Delete ${name} with its test data and its files for upload steps? This cannot be undone.`;

function newDraft(kind: Kind): Draft {
  return { name: null, kind, gherkin: kind === "feature" ? NEW_FEATURE : "", form: emptyForm(), data: [], base: undefined, dirty: true };
}

/** Whether leaving the draft loses something the tester typed (a fresh new case does not count). */
function draftIsEdited(draft: Draft): boolean {
  if (!draft.dirty) return false;
  if (draft.name !== null) return true;
  const fresh = newDraft(draft.kind);
  return draft.gherkin !== fresh.gherkin || JSON.stringify(draft.form) !== JSON.stringify(fresh.form) || draft.data.length > 0;
}

function CaseList({ cases, selected, onNew, onOpen }: { cases: CaseEntry[]; selected: string | null; onNew: (kind: Kind) => void; onOpen: (event: MouseEvent) => void }) {
  return (
    <nav className="panel case-list" aria-label="Saved cases">
      <div className="panel-head">
        <h2>Saved cases</h2>
        <span className="muted small">{cases.length}</span>
      </div>
      <div className="case-new">
        <button type="button" className="button button-small" onClick={() => onNew("feature")}>
          <Icon name="plus" size={13} /> Gherkin
        </button>
        <button type="button" className="button button-small button-ghost" onClick={() => onNew("json")}>
          <Icon name="plus" size={13} /> Form
        </button>
      </div>
      {cases.length === 0 ? <p className="muted small">No cases in .hercules/cases yet.</p> : null}
      <ul>
        {cases.map((entry) => (
          <li key={entry.name}>
            <Link href={`/cases/${encodeURIComponent(entry.name)}`} className={`case-item${entry.name === selected ? " case-item-on" : ""}`} title={entry.name} onClick={onOpen} aria-current={entry.name === selected ? "page" : undefined}>
              <span className="case-item-title">{entry.title ?? entry.name}</span>
              <span className="case-item-meta">
                {entry.valid ? <span>
                    {entry.steps} {entry.steps === 1 ? "step" : "steps"}
                  </span> : <span className="text-fail">invalid</span>}
                {entry.status === "draft" ? <span className="tag">draft</span> : null}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function GherkinEnvironment({ gherkin, environments, onChange }: { gherkin: string; environments: string[]; onChange: (gherkin: string) => void }) {
  const [open, setOpen] = useState(false);
  const chosen = environmentTag(gherkin);
  return (
    <div className="gherkin-env">
      <div className="gherkin-env-bar">
        <span className="muted small">
          Environment: {chosen ? <code>{chosen}</code> : "none (the one picked when you run it, or the steps' own URLs)"}
          {chosen && !environments.includes(chosen) ? <span className="text-fail"> · not saved</span> : null}
        </span>
        <button type="button" className="icon-button" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="gherkin-env-help" aria-label="How to choose an environment" title="How to add an environment to the scenario">
          <Icon name="help" size={16} />
        </button>
      </div>
      {open ? (
        <div id="gherkin-env-help" className="callout callout-note gherkin-env-help">
          <p>
            Add an <code>@env:&lt;name&gt;</code> tag above <code>Feature:</code> to run the scenario in a saved environment:
          </p>
          <pre className="mono">{"@smoke @env:shop\nFeature: Sign in\n  Scenario: Registered user signs in\n    Given I open \"/login\"\n    When I fill \"{{user}}\" in \"Email\""}</pre>
          <ul>
            <li>The environment gives the base URL (so <code>&quot;/login&quot;</code> opens on it), the browser, phone or native app, and its variables and secrets.</li>
            <li>
              Use its variables and secrets in steps as <code>{"{{name}}"}</code>. Values only this case needs go in the Test data on the Form tab.
            </li>
            <li>
              <code>@device:iPhone_15</code> emulates a phone in the browser (<code>_</code> for spaces).
            </li>
            <li>An environment picked when you start a run replaces the tag for that run.</li>
          </ul>
          {environments.length ? (
            <div className="gherkin-env-choices">
              <span className="small">Use a saved environment:</span>
              {environments.map((name) => (
                <button key={name} type="button" className={`button button-small${name === chosen ? " button-on" : ""}`} onClick={() => onChange(withEnvironmentTag(gherkin, name))} aria-pressed={name === chosen}>
                  @env:{name}
                </button>
              ))}
            </div>
          ) : (
            <p className="small">No environments are saved yet: add one on the Environments screen.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

function quotedHint(tokens: string[]): string {
  return tokens.map((token) => `${token} → "${token}"`).join(", ");
}

function withQuotedPlaceholders(draft: Draft): Draft {
  return draft.kind === "feature" ? { ...draft, gherkin: quoteGherkinPlaceholders(draft.gherkin), dirty: true } : { ...draft, form: quoteFormPlaceholders(draft.form), dirty: true };
}

export function CasesScreen({ selected }: { selected?: string }) {
  const client = useQueryClient();
  const [, navigate] = useLocation();
  const name = caseNameFromParam(selected);
  const list = useQuery({ queryKey: ["cases"], queryFn: () => api<{ cases: CaseEntry[] }>("/api/cases", { guard: isCaseList }) });
  const saved = useQuery({ queryKey: ["case", name], queryFn: () => api<SavedCase>(`/api/cases/${encodeURIComponent(name ?? "")}`, { guard: isSavedCase }), enabled: Boolean(name) });
  const [draft, setDraft] = useState<Draft>(() => newDraft("feature"));
  const [tab, setTab] = useState<Tab>("gherkin");
  const [notice, setNotice] = useState<string | null>(null);
  const [running, setRunning] = useState<StartRunInitial | null>(null);
  const [showFormErrors, setShowFormErrors] = useState(false);
  const [session, setSession] = useState(0);
  const [quotePrompt, setQuotePrompt] = useState(false);
  // The kind of new case asked for while a saved one was open: applied once the route is /cases.
  const pendingNew = useRef<Kind | null>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  useEffect(() => {
    if (!name) {
      const kind = pendingNew.current ?? "feature";
      pendingNew.current = null;
      setDraft(newDraft(kind));
      setSession((value) => value + 1);
      setTab(kind === "feature" ? "gherkin" : "form");
      setShowFormErrors(false);
      return;
    }
    if (!saved.data || saved.data.name !== name) return;
    const current = draftRef.current;
    if (current.name === name && current.dirty) {
      // A refetch (window focus, another tab saving) must not wipe what is being typed.
      setNotice("The saved file changed while you were editing; saving replaces it with your version.");
      return;
    }
    const kind: Kind = saved.data.kind === "feature" ? "feature" : "json";
    const form = toForm(saved.data.test_case);
    if (current.name !== name) setSession((value) => value + 1);
    setDraft({ name, kind, gherkin: kind === "feature" ? (saved.data.source ?? "") : "", form, data: kind === "feature" ? form.testData : [], base: saved.data.test_case, dirty: false });
    setTab(kind === "feature" ? "gherkin" : "form");
    setNotice(null);
    setShowFormErrors(false);
  }, [name, saved.data]);

  // Closing or reloading the tab with unsaved edits asks first.
  const edited = draftIsEdited(draft);
  useEffect(() => {
    if (!edited) return undefined;
    const onBeforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [edited]);

  /** True when there is nothing unsaved, or the tester agrees to drop it. */
  const mayDiscard = () => !draftIsEdited(draftRef.current) || window.confirm(DISCARD_PROMPT);

  // Gherkin is checked by the daemon (the same compiler the run uses) while typing.
  const gherkin = useDebounced(draft.kind === "feature" ? draft.gherkin : "", 400);
  const featureData = useDebounced(JSON.stringify(draft.kind === "feature" ? testDataOf(validTestData(draft.data)) : {}), 400);
  const check = useQuery({
    queryKey: ["validate", gherkin, featureData],
    queryFn: () => post<CaseCheck>("/api/cases/validate", { gherkin, test_data: JSON.parse(featureData) }),
    enabled: draft.kind === "feature" && gherkin.trim().length > 0,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  const compiled = draft.kind === "feature" ? (check.data?.test_case ?? null) : null;
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => api<{ environments: Environment[] }>("/api/environments") });
  const gherkinEnvironment = draft.kind === "feature" ? environmentTag(draft.gherkin) : draft.form.environmentRef.trim();
  const suggestions = useMemo(
    () => placeholderSuggestions(envs.data?.environments.find((env) => env.name === gherkinEnvironment), draft.kind === "feature" ? draft.data : draft.form.testData),
    [envs.data, gherkinEnvironment, draft.kind, draft.data, draft.form.testData],
  );
  const environmentSuggestions = useMemo(() => (envs.data?.environments ?? []).map((env) => ({ name: env.name, detail: targetSummary(env) })), [envs.data]);
  const savedEnvironments = useMemo(() => (envs.data?.environments ?? []).map((env) => env.name), [envs.data]);
  const envNames = useMemo(() => environmentNames(envs.data?.environments.find((env) => env.name === draft.form.environmentRef.trim())), [envs.data, draft.form.environmentRef]);
  const formErrors = useMemo(() => (draft.kind === "json" ? validateCaseForm(draft.form, envNames) : {}), [draft.kind, draft.form, envNames]);
  const formValid = Object.keys(formErrors).length === 0;
  const dataErrors = useMemo(() => (draft.kind === "feature" ? validateTestData(draft.data) : {}), [draft.kind, draft.data]);
  const dataValid = Object.keys(dataErrors).length === 0;

  const unquoted = useMemo(() => (draft.kind === "feature" ? gherkinUnquoted(draft.gherkin) : formUnquoted(draft.form)), [draft.kind, draft.gherkin, draft.form]);
  const save = useMutation({
    mutationFn: async ({ overwrite, current }: { overwrite: boolean; current: Draft }) => {
      const body =
        current.kind === "feature" ? { gherkin: current.gherkin, test_data: testDataOf(current.data), overwrite } : { test_case: fromForm(current.form, current.base), overwrite };
      return post<{ name: string; path: string; id: string }>("/api/cases", body);
    },
    onSuccess: (result) => {
      setNotice(`Saved ${result.path}`);
      void client.invalidateQueries({ queryKey: ["cases"] });
      void client.invalidateQueries({ queryKey: ["case", result.name] });
      setDraft((current) => ({ ...current, name: result.name, dirty: false }));
      if (result.name !== name) navigate(`/cases/${encodeURIComponent(result.name)}`);
    },
  });

  // A 409 "Replace it" belongs to the case it was raised for, not to the next one opened.
  const resetSave = save.reset;
  useEffect(() => resetSave(), [name, resetSave]);

  const onSave = (quotes: "ask" | "add" | "keep" = "ask") => {
    setNotice(null);
    if ((draft.kind === "json" && !formValid) || (draft.kind === "feature" && !dataValid)) {
      setShowFormErrors(true);
      if (draft.kind === "feature") setTab("form");
      return;
    }
    if (quotes === "ask" && unquoted.length) {
      setQuotePrompt(true);
      return;
    }
    setQuotePrompt(false);
    const current = quotes === "add" ? withQuotedPlaceholders(draft) : draft;
    if (current !== draft) setDraft(current);
    const sameFile = current.name !== null && (current.kind === "feature" ? compiled?.id && `${compiled.id}.feature` === current.name : `${current.form.id.trim()}.json` === current.name);
    save.mutate({ overwrite: Boolean(sameFile), current });
  };

  const saveError = save.error instanceof ApiError ? save.error : null;
  const gherkinError = check.data && !check.data.valid ? check.data.error : null;
  const entry = list.data?.cases.find((item) => item.name === name);

  const drop = useMutation({
    mutationFn: (file: string) => remove<{ name: string; removed: boolean }>(`/api/cases/${encodeURIComponent(file)}`),
    onSuccess: (result) => {
      client.removeQueries({ queryKey: ["case", result.name] });
      void client.invalidateQueries({ queryKey: ["cases"] });
      setDraft((current) => ({ ...current, dirty: false }));
      navigate("/cases");
      setNotice(`Deleted ${result.name}`);
    },
  });

  const onDelete = () => {
    if (draft.name && window.confirm(deleteCasePrompt(draft.name))) drop.mutate(draft.name);
  };

  const onRun = () => {
    if (draft.kind === "feature" && (draft.dirty || !entry)) setRunning({ source: "gherkin", gherkin: draft.gherkin, testData: testDataOf(validTestData(draft.data)) });
    else if (entry) setRunning({ source: "saved", casePath: entry.path });
  };

  return (
    <section className="page page-cases" aria-labelledby="cases-title">
      <header className="page-head">
        <div>
          <h1 id="cases-title">Cases</h1>
          <p className="page-sub">Test cases in .hercules/cases: Gherkin scenarios or TestCase JSON.</p>
        </div>
      </header>
      <div className="cases-grid">
        <CaseList
          cases={list.data?.cases ?? []}
          selected={name}
          onOpen={(event) => {
            if (!mayDiscard()) event.preventDefault();
          }}
          onNew={(kind) => {
            if (!mayDiscard()) return;
            save.reset();
            setNotice(null);
            if (name) {
              pendingNew.current = kind; // the route change to /cases creates the draft
              navigate("/cases");
              return;
            }
            setDraft(newDraft(kind));
            setTab(kind === "feature" ? "gherkin" : "form");
            setShowFormErrors(false);
          }}
        />
        <div className="panel case-editor">
          <div className="panel-head">
            <h2>
              {draft.name ?? (draft.kind === "feature" ? "New Gherkin case" : "New case")}
              {draft.dirty ? <span className="muted small"> · unsaved</span> : null}
            </h2>
            <div className="editor-actions">
              <button type="button" className="button" onClick={onRun} disabled={draft.kind === "json" && (draft.dirty || !entry)} title={draft.kind === "json" && draft.dirty ? "Save the case first" : undefined}>
                <Icon name="play" size={13} /> Run
              </button>
              <button type="button" className="button button-primary" onClick={() => onSave()} disabled={save.isPending || (draft.kind === "feature" && !draft.gherkin.trim())}>
                <Icon name="save" size={14} /> {save.isPending ? "Saving…" : "Save"}
              </button>
              {draft.name ? (
                <button type="button" className="button button-danger" onClick={onDelete} disabled={drop.isPending}>
                  Delete
                </button>
              ) : null}
            </div>
          </div>
          {saved.isError && name ? <p className="callout callout-fail">Could not open {name}: {(saved.error as Error).message}</p> : null}
          {entry && !entry.valid ? <p className="callout callout-fail">This file does not load: {entry.error}</p> : null}

          <div className="tabs" role="tablist" aria-label="Editor view">
            {(draft.kind === "feature" ? (["gherkin", "form", "json"] as const) : (["form", "json"] as const)).map((id) => (
              <button key={id} type="button" role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls={`panel-${id}`} className={`tab${tab === id ? " tab-on" : ""}`} onClick={() => setTab(id)}>
                {id === "gherkin" ? "Gherkin" : id === "form" ? "Form" : "TestCase JSON"}
              </button>
            ))}
          </div>

          {tab === "gherkin" && draft.kind === "feature" ? (
            <div role="tabpanel" id="panel-gherkin" aria-labelledby="tab-gherkin">
              <GherkinEnvironment gherkin={draft.gherkin} environments={savedEnvironments} onChange={(text) => setDraft({ ...draft, gherkin: text, dirty: true })} />
              <GherkinEditor key={session} value={draft.gherkin} onChange={(text) => setDraft({ ...draft, gherkin: text, dirty: true })} errorLine={errorLine(gherkinError ?? saveError?.message)}
                suggestions={suggestions}
                environments={environmentSuggestions} rows={20} describedBy="gherkin-status" />
              <p id="gherkin-status" className={`lint ${gherkinError ? "lint-bad" : "lint-ok"}`} role="status" aria-live="polite">
                {!draft.gherkin.trim() ? "Empty." : gherkinError ? (
                  <>
                    <Icon name="alert" size={13} /> {gherkinError}
                  </>
                ) : compiled ? (
                  <>
                    <Icon name="check" size={13} /> Valid: {compiled.steps.length} steps, case id <code>{compiled.id}</code>
                  </>
                ) : (
                  "Checking…"
                )}
              </p>
              {unquoted.length ? (
                <p className="lint lint-warn" role="note">
                  <Icon name="alert" size={13} /> Put placeholder values in quotes:{" "}
                  {unquoted.map((found) => `line ${found.line}: ${quotedHint(found.tokens)}`).join("; ")}
                </p>
              ) : null}
            </div>
          ) : null}

          {tab === "form" ? (
            <div role="tabpanel" id="panel-form" aria-labelledby="tab-form">
              {draft.kind === "feature" ? (
                <>
                  {compiled ? (
                    <>
                      <p className="callout callout-note">This is how the daemon reads the Gherkin. Edit the Gherkin to change it; the test data below is saved beside it.</p>
                      <fieldset disabled className="readonly">
                        <CaseForm value={toForm(compiled)} errors={{}} onChange={() => undefined} showTestData={false} environments={savedEnvironments} />
                      </fieldset>
                    </>
                  ) : (
                    <p className="muted">Fix the Gherkin to see its structure. Add the values its {"{{placeholders}}"} use below.</p>
                  )}
                  <TestDataEditor rows={draft.data} errors={showFormErrors ? dataErrors : {}} onChange={(data) => setDraft({ ...draft, data, dirty: true })} />
                </>
              ) : (
                <CaseForm value={draft.form} errors={showFormErrors ? formErrors : {}} onChange={(form) => setDraft({ ...draft, form, dirty: true })} environments={savedEnvironments} suggestions={suggestions} />
              )}
              <CaseFiles key={session} name={draft.name} />
            </div>
          ) : null}

          {tab === "json" ? (
            <div role="tabpanel" id="panel-json" aria-labelledby="tab-json">
              <pre className="json-view">{JSON.stringify(draft.kind === "feature" ? compiled : fromForm(draft.form, draft.base), null, 2)}</pre>
            </div>
          ) : null}

          {drop.error ? (
            <p className="callout callout-fail" role="alert">
              {(drop.error as Error).message}
            </p>
          ) : null}
          {quotePrompt && unquoted.length ? (
            <div className="callout callout-warn" role="alert">
              <p>
                {unquoted.length === 1 ? "A step uses" : "Some steps use"} a placeholder without quotes ({unquoted.map((found) => `${draft.kind === "feature" ? "line" : "step"} ${found.line}: ${found.tokens.join(", ")}`).join("; ")}). Steps read
                values most reliably in quotes, as in <code>{'When I fill "{{name}}" in "Name"'}</code>.
              </p>
              <div className="editor-actions">
                <button type="button" className="button button-small button-primary" onClick={() => onSave("add")}>
                  Add quotes and save
                </button>
                <button type="button" className="button button-small" onClick={() => onSave("keep")}>
                  Save as is
                </button>
              </div>
            </div>
          ) : null}
          {saveError ? (
            <p className="callout callout-fail" role="alert">
              {saveError.message}
              {saveError.status === 409 ? (
                <button type="button" className="link-button" onClick={() => save.mutate({ overwrite: true, current: draft })}>
                  Replace it
                </button>
              ) : null}
            </p>
          ) : null}
          {showFormErrors && !formValid && draft.kind === "json" ? <p className="callout callout-fail">Fix the highlighted fields before saving.</p> : null}
          {notice ? (
            <p className="notice" role="status">
              {notice}
            </p>
          ) : null}
        </div>
      </div>
      {running ? (
        <StartRunDialog
          initial={running}
          onClose={() => setRunning(null)}
          onStarted={(runId) => {
            setRunning(null);
            navigate(`/runs/${runId}`);
          }}
        />
      ) : null}
    </section>
  );
}
