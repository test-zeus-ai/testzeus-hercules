import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "wouter";
import { CaseForm, TestDataEditor } from "../components/CaseForm";
import { CaseTree } from "../components/CaseTree";
import { GherkinEditor } from "../components/GherkinEditor";
import { Icon } from "../components/Icon";
import { placeholderSuggestions } from "../components/PlaceholderSuggest";
import { CaseFiles } from "../components/StepFiles";
import { StartRunDialog, type StartRunInitial } from "../components/StartRunDialog";
import { ApiError, api, post, remove } from "../lib/api";
import { emptyForm, environmentTag, errorLine, formUnquoted, gherkinUnquoted, quoteFormPlaceholders, quoteGherkinPlaceholders, fromForm, savedNamesIn, testDataOf, toForm, validTestData, validateCaseForm, validateTestData, withEnvironmentTag, type CaseFormValue, type FormDatum } from "../lib/caseForm";
import { environmentNames, targetSummary } from "../lib/environmentForm";
import { useDebounced } from "../lib/useDebounced";
import { folderOptions, joinPath, leafOf, parentOf, pathUrl, relocated } from "../lib/caseTree";
import { isCaseList, isSavedCase } from "../lib/guards";
import type { CaseCheck, CaseListing, Environment, SavedCase, TestCase } from "../lib/types";

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
  name: string | null; // saved case path, null for a new case
  folder: string;
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

/** The case path of a `/cases/*` route param. */
export function caseNameFromParam(param: string | undefined): string | null {
  if (!param) return null;
  return param.replace(RESERVED_ESCAPE, (_match, hex: string) => String.fromCharCode(Number.parseInt(hex, 16)));
}

export const DISCARD_PROMPT = "Discard the unsaved changes to this case?";

/** The editor's heading: the case's title; the file name only when the case has no title yet. */
export function caseHeading(name: string | null, title: string | null | undefined, kind: "feature" | "json"): string {
  const shown = title?.trim();
  if (shown) return shown;
  return name ?? (kind === "feature" ? "New Gherkin case" : "New case");
}

export const deleteCasePrompt = (name: string) => `Delete ${name} with its test data and its files for upload steps? This cannot be undone.`;

const PANEL_KEY = "hercules.cases.panel";

function newDraft(kind: Kind, folder = ""): Draft {
  return { name: null, folder, kind, gherkin: kind === "feature" ? NEW_FEATURE : "", form: emptyForm(), data: [], base: undefined, dirty: true };
}

/** Whether leaving the draft loses something the tester typed (a fresh new case does not count). */
function draftIsEdited(draft: Draft): boolean {
  if (!draft.dirty) return false;
  if (draft.name !== null) return true;
  const fresh = newDraft(draft.kind, draft.folder);
  return draft.gherkin !== fresh.gherkin || JSON.stringify(draft.form) !== JSON.stringify(fresh.form) || draft.data.length > 0;
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
  const list = useQuery({ queryKey: ["cases"], queryFn: () => api<CaseListing>("/api/cases", { guard: isCaseList }) });
  const saved = useQuery({ queryKey: ["case", name], queryFn: () => api<SavedCase>(`/api/cases/${pathUrl(name ?? "")}`, { guard: isSavedCase }), enabled: Boolean(name) });
  const [panelHidden, setPanelHidden] = useState(() => {
    try {
      return window.localStorage.getItem(PANEL_KEY) === "hidden";
    } catch {
      return false;
    }
  });
  const [draft, setDraft] = useState<Draft>(() => newDraft("feature"));
  const [tab, setTab] = useState<Tab>("gherkin");
  const [notice, setNotice] = useState<string | null>(null);
  const [running, setRunning] = useState<StartRunInitial | null>(null);
  const [showFormErrors, setShowFormErrors] = useState(false);
  const [session, setSession] = useState(0);
  const [quotePrompt, setQuotePrompt] = useState(false);
  // The new case asked for while a saved one was open: applied once the route is /cases.
  const pendingNew = useRef<{ kind: Kind; folder: string } | null>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  useEffect(() => {
    if (!name) {
      const { kind, folder } = pendingNew.current ?? { kind: "feature" as Kind, folder: "" };
      pendingNew.current = null;
      setDraft(newDraft(kind, folder));
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
    setDraft({ name, folder: saved.data.folder ?? parentOf(name), kind, gherkin: kind === "feature" ? (saved.data.source ?? "") : "", form, data: kind === "feature" ? form.testData : [], base: saved.data.test_case, dirty: false });
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
    () =>
      placeholderSuggestions(
        envs.data?.environments.find((env) => env.name === gherkinEnvironment),
        draft.kind === "feature" ? draft.data : draft.form.testData,
        draft.kind === "feature" ? savedNamesIn(draft.gherkin) : draft.form.steps.flatMap((step) => savedNamesIn(step.action)),
      ),
    [envs.data, gherkinEnvironment, draft.kind, draft.data, draft.form.testData, draft.gherkin, draft.form.steps],
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
        current.kind === "feature"
          ? { gherkin: current.gherkin, test_data: testDataOf(current.data), overwrite, folder: current.folder }
          : { test_case: fromForm(current.form, current.base), overwrite, folder: current.folder };
      return post<{ name: string; path: string; id: string }>("/api/cases", body);
    },
    onSuccess: (result) => {
      setNotice(`Saved ${result.path}`);
      void client.invalidateQueries({ queryKey: ["cases"] });
      void client.invalidateQueries({ queryKey: ["case", result.name] });
      setDraft((current) => ({ ...current, name: result.name, dirty: false }));
      if (result.name !== name) navigate(`/cases/${pathUrl(result.name)}`);
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
    const sameFile =
      current.name !== null && (current.kind === "feature" ? compiled?.id && joinPath(current.folder, `${compiled.id}.feature`) === current.name : joinPath(current.folder, `${current.form.id.trim()}.json`) === current.name);
    save.mutate({ overwrite: Boolean(sameFile), current });
  };

  const saveError = save.error instanceof ApiError ? save.error : null;
  const gherkinError = check.data && !check.data.valid ? check.data.error : null;
  const entry = list.data?.cases.find((item) => item.name === name);

  const drop = useMutation({
    mutationFn: (file: string) => remove<{ name: string; removed: boolean }>(`/api/cases/${pathUrl(file)}`),
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

  const startNew = (kind: Kind, folder: string) => {
    if (!mayDiscard()) return;
    save.reset();
    setNotice(null);
    if (name) {
      pendingNew.current = { kind, folder }; // the route change to /cases creates the draft
      navigate("/cases");
      return;
    }
    setDraft(newDraft(kind, folder));
    setSession((value) => value + 1);
    setTab(kind === "feature" ? "gherkin" : "form");
    setShowFormErrors(false);
  };

  const onRelocate = (from: string, to: string | null) => {
    void client.invalidateQueries({ queryKey: ["cases"] });
    if (!draft.name) {
      const folder = relocated(draft.folder, from, to);
      if (folder !== draft.folder) setDraft((current) => ({ ...current, folder: folder ?? parentOf(from) }));
      return;
    }
    const moved = relocated(draft.name, from, to);
    if (moved === draft.name) return;
    client.removeQueries({ queryKey: ["case", draft.name] });
    if (moved === null) {
      setDraft((current) => ({ ...current, dirty: false }));
      navigate("/cases");
      return;
    }
    setDraft((current) => ({ ...current, name: moved, folder: parentOf(moved) }));
    navigate(`/cases/${pathUrl(moved)}`, { replace: true });
  };

  const [panelMoving, setPanelMoving] = useState(false);
  useEffect(() => {
    if (!panelMoving) return undefined;
    const timer = window.setTimeout(() => setPanelMoving(false), 320);
    return () => window.clearTimeout(timer);
  }, [panelMoving, panelHidden]);

  const hidePanel = (hidden: boolean) => {
    setPanelMoving(true);
    setPanelHidden(hidden);
    try {
      window.localStorage.setItem(PANEL_KEY, hidden ? "hidden" : "shown");
    } catch {
      return;
    }
  };

  const folders = list.data?.folders ?? [];
  const newFolder = draft.folder;

  const onRun = () => {
    if (draft.kind === "feature" && (draft.dirty || !entry)) setRunning({ source: "gherkin", gherkin: draft.gherkin, testData: testDataOf(validTestData(draft.data)) });
    else if (entry) setRunning({ source: "saved", casePath: entry.path });
  };


  return (
    <section className="page page-cases" aria-labelledby="cases-title">
      <header className="page-head">
        <div>
          <h1 id="cases-title">Cases</h1>
          <p className="page-sub">Test cases in .hercules/cases and its folders: Gherkin scenarios or TestCase JSON.</p>
        </div>
        <div className="editor-actions">
          <button type="button" className="button" onClick={() => startNew("json", newFolder)}>
            <Icon name="plus" size={13} /> Form case
          </button>
          <button type="button" className="button button-primary" onClick={() => startNew("feature", newFolder)}>
            <Icon name="plus" size={13} /> Gherkin case
          </button>
        </div>
      </header>
      <div className={`cases-grid${panelHidden ? " cases-grid-collapsed" : ""}${panelMoving ? " cases-grid-moving" : ""}`}>
        <CaseTree
          cases={list.data?.cases ?? []}
          folders={folders}
          selected={name}
          collapsed={panelHidden}
          onCollapse={hidePanel}
          onOpen={(event) => {
            if (!mayDiscard()) event.preventDefault();
          }}
          onNewCase={(folder, kind) => startNew(kind, folder)}
          onRunCase={(chosen) => setRunning({ source: "saved", casePath: chosen.path })}
          onRunFolder={(folder) => setRunning({ source: "folder", folder })}
          onRelocate={onRelocate}
        />
        <div className="panel case-editor">
          <div className="panel-head">
            <div className="case-heading">
              <h2 title={draft.name ?? undefined}>
                {caseHeading(draft.name ? leafOf(draft.name) : null, entry?.title ?? (draft.kind === "feature" ? compiled?.title : draft.form.title), draft.kind)}
                {draft.dirty ? <span className="muted small"> · unsaved</span> : null}
              </h2>
              {draft.name ? (
                <p className="case-path mono">
                  {["cases", ...(draft.folder ? draft.folder.split("/") : []), leafOf(draft.name)].join(" / ")}
                </p>
              ) : (
                <label className="case-path case-path-pick">
                  <span>Saves into</span>
                  <select value={draft.folder} onChange={(event) => setDraft({ ...draft, folder: event.target.value })}>
                    <option value="">the top level</option>
                    {folderOptions(folders).map((option) => (
                      <option key={option.path} value={option.path}>
                        {option.path}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
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
          onStarted={(runId, batch) => {
            setRunning(null);
            navigate(batch ? `/runs?batch=${encodeURIComponent(batch)}` : `/runs/${runId}`);
          }}
        />
      ) : null}
    </section>
  );
}
