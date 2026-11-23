import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ExportFormat, ExportView } from "../lib/types";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { Snippet } from "./Snippet";

const SPEC = { id: "spec", label: "Automation spec", hint: "The framework-neutral spec, for a coding agent to write the test from" } as const;
const WEB_FORMATS: ReadonlyArray<{ id: ExportFormat; label: string; hint: string }> = [
  { id: "playwright-ts", label: "Playwright (TypeScript)", hint: "A Playwright Test file (@playwright/test)" },
  { id: "playwright-py", label: "pytest (Python)", hint: "A pytest-playwright file (sync API)" },
  SPEC,
];
/** A native app run (on a device) exports as Appium code, never as Playwright. */
const APP_FORMATS: ReadonlyArray<{ id: ExportFormat; label: string; hint: string }> = [
  { id: "appium-py", label: "Appium (pytest)", hint: "A pytest file with the Appium Python client" },
  SPEC,
];

/** The last path segment of a suggested file name, for the download. */
export function downloadName(filename: string): string {
  return filename.split("/").pop() || "hercules-export.txt";
}

function Download({ text, filename }: { text: string; filename: string }) {
  const [href, setHref] = useState<string | null>(null);
  useEffect(() => {
    if (typeof URL.createObjectURL !== "function") return undefined;
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    setHref(url);
    return () => URL.revokeObjectURL(url);
  }, [text]);
  if (!href) return null;
  return (
    <a className="button button-small" href={href} download={downloadName(filename)}>
      <Icon name="save" size={13} /> Download {downloadName(filename)}
    </a>
  );
}

/** "Export as code" for a passed run: pick a format, read the generated test, copy or download it. */
export function ExportDialog({ runId, native = false, onClose }: { runId: string; native?: boolean; onClose: () => void }) {
  const FORMATS = native ? APP_FORMATS : WEB_FORMATS;
  const [format, setFormat] = useState<ExportFormat>(FORMATS[0].id);
  const exported = useQuery({
    queryKey: ["export", runId, format],
    queryFn: () => api<ExportView>(`/api/runs/${encodeURIComponent(runId)}/export?format=${encodeURIComponent(format)}`),
    staleTime: Infinity,
    retry: false,
  });
  const view = exported.data;
  return (
    <Modal title="Export as code" onClose={onClose} wide>
      <div className="export-dialog">
        <div className="segmented" role="group" aria-label="Format">
          {FORMATS.map((item) => (
            <button key={item.id} type="button" className={`segment${format === item.id ? " segment-on" : ""}`} aria-pressed={format === item.id} title={item.hint} onClick={() => setFormat(item.id)}>
              {item.label}
            </button>
          ))}
        </div>
        {exported.isPending ? <p className="muted">Generating…</p> : null}
        {exported.isError ? (
          <p className="callout callout-fail" role="alert">
            {(exported.error as Error).message}
          </p>
        ) : null}
        {view ? (
          <>
            <p className="muted small">{view.guidance}</p>
            <div className="export-meta">
              <span>
                Suggested file <span className="mono">{view.filename}</span>
              </span>
              <span>
                {view.steps_exported} of {view.steps_total} steps
              </span>
              <Download text={view.code} filename={view.filename} />
            </div>
            {view.env.length ? (
              <ul className="export-env" aria-label="Environment variables">
                {view.env.map((item) => (
                  <li key={item.name}>
                    <span className="mono">{item.name}</span> <span className="muted">({item.required ? "required" : "optional"}): {item.purpose}</span>
                  </li>
                ))}
              </ul>
            ) : null}
            {view.notes.length ? (
              <div className="callout callout-note">
                <strong>Review before keeping it</strong>
                <ul className="export-notes">
                  {view.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="export-code">
              <Snippet text={view.code} label={`the ${FORMATS.find((item) => item.id === view.format)?.label ?? "exported"} code`} />
            </div>
          </>
        ) : null}
      </div>
    </Modal>
  );
}
