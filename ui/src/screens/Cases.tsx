import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { Link, useLocation } from "wouter";
import { CaseForm } from "../components/CaseForm";
import { GherkinEditor } from "../components/GherkinEditor";
import { Icon } from "../components/Icon";
import { StartRunDialog, type StartRunInitial } from "../components/StartRunDialog";
import { ApiError, api, post } from "../lib/api";
import { emptyForm, errorLine, fromForm, toForm, validateCaseForm, type CaseFormValue } from "../lib/caseForm";
import { useDebounced } from "../lib/useDebounced";
import { isCaseList, isSavedCase } from "../lib/guards";
import type { CaseCheck, CaseEntry, SavedCase, TestCase } from "../lib/types";

type Kind = "feature" | "json";
type Tab = "gherkin" | "form" | "json";

// A .feature file has no test data, so the template uses no {{placeholders}} (a password belongs in a JSON case's
// test_data, as a secret reference).
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

function newDraft(kind: Kind): Draft {
  return { name: null, kind, gherkin: kind === "feature" ? NEW_FEATURE : "", form: emptyForm(), base: undefined, dirty: true };
}

/** Whether leaving the draft loses something the tester typed (a fresh new case does not count). */
function draftIsEdited(draft: Draft): boolean {
  if (!draft.dirty) return false;
  if (draft.name !== null) return true;
  const fresh = newDraft(draft.kind);
  return draft.gherkin !== fresh.gherkin || JSON.stringify(draft.form) !== JSON.stringify(fresh.form);
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
            <Link href={`/cases/${encodeURIComponent(entry.name)}`} className={`case-item${entry.name === selected ? " case-item-on" : ""}`} onClick={onOpen} aria-current={entry.name === selected ? "page" : undefined}>
              <span className="case-item-title">{entry.title ?? entry.name}</span>
              <span className="case-item-meta">
                <span className="mono">{entry.name}</span>
                {entry.valid ? <span>{entry.steps} steps</span> : <span className="text-fail">invalid</span>}
                {entry.status === "draft" ? <span className="tag">draft</span> : null}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
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
  // The kind of new case asked for while a saved one was open: applied once the route is /cases.
  const pendingNew = useRef<Kind | null>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  useEffect(() => {
    if (!name) {
      const kind = pendingNew.current ?? "feature";
      pendingNew.current = null;
      setDraft(newDraft(kind));
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
    setDraft({ name, kind, gherkin: kind === "feature" ? (saved.data.source ?? "") : "", form: toForm(saved.data.test_case), base: saved.data.test_case, dirty: false });
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
  const check = useQuery({
    queryKey: ["validate", gherkin],
    queryFn: () => post<CaseCheck>("/api/cases/validate", { gherkin }),
    enabled: draft.kind === "feature" && gherkin.trim().length > 0,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  const compiled = draft.kind === "feature" ? (check.data?.test_case ?? null) : null;
  const formErrors = useMemo(() => (draft.kind === "json" ? validateCaseForm(draft.form) : {}), [draft.kind, draft.form]);
  const formValid = Object.keys(formErrors).length === 0;

  const save = useMutation({
    mutationFn: async (overwrite: boolean) => {
      const body = draft.kind === "feature" ? { gherkin: draft.gherkin, overwrite } : { test_case: fromForm(draft.form, draft.base), overwrite };
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

  const onSave = () => {
    setNotice(null);
    if (draft.kind === "json" && !formValid) {
      setShowFormErrors(true);
      return;
    }
    const sameFile = draft.name !== null && (draft.kind === "feature" ? compiled?.id && `${compiled.id}.feature` === draft.name : `${draft.form.id.trim()}.json` === draft.name);
    save.mutate(Boolean(sameFile));
  };

  const saveError = save.error instanceof ApiError ? save.error : null;
  const gherkinError = check.data && !check.data.valid ? check.data.error : null;
  const entry = list.data?.cases.find((item) => item.name === name);

  const onRun = () => {
    if (draft.kind === "feature" && (draft.dirty || !entry)) setRunning({ source: "gherkin", gherkin: draft.gherkin });
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
              <button type="button" className="button button-primary" onClick={onSave} disabled={save.isPending || (draft.kind === "feature" && !draft.gherkin.trim())}>
                <Icon name="save" size={14} /> {save.isPending ? "Saving…" : "Save"}
              </button>
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
              <GherkinEditor value={draft.gherkin} onChange={(text) => setDraft({ ...draft, gherkin: text, dirty: true })} errorLine={errorLine(gherkinError ?? saveError?.message)} rows={20} describedBy="gherkin-status" />
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
            </div>
          ) : null}

          {tab === "form" ? (
            <div role="tabpanel" id="panel-form" aria-labelledby="tab-form">
              {draft.kind === "feature" ? (
                compiled ? (
                  <>
                    <p className="callout callout-note">This is how the daemon reads the Gherkin. Edit the Gherkin to change it.</p>
                    <fieldset disabled className="readonly">
                      <CaseForm value={toForm(compiled)} errors={{}} onChange={() => undefined} />
                    </fieldset>
                  </>
                ) : (
                  <p className="muted">Fix the Gherkin to see its structure.</p>
                )
              ) : (
                <CaseForm value={draft.form} errors={showFormErrors ? formErrors : {}} onChange={(form) => setDraft({ ...draft, form, dirty: true })} />
              )}
            </div>
          ) : null}

          {tab === "json" ? (
            <div role="tabpanel" id="panel-json" aria-labelledby="tab-json">
              <pre className="json-view">{JSON.stringify(draft.kind === "feature" ? compiled : fromForm(draft.form, draft.base), null, 2)}</pre>
            </div>
          ) : null}

          {saveError ? (
            <p className="callout callout-fail" role="alert">
              {saveError.message}
              {saveError.status === 409 ? (
                <button type="button" className="link-button" onClick={() => save.mutate(true)}>
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
