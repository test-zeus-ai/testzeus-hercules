import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { api, remove, upload } from "../lib/api";
import type { ReferenceFiles } from "../lib/types";

const ACCEPT = ".md,.markdown,.txt,.pdf,.docx,.csv,.json,.png,.jpg,.jpeg";

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type Owner = "environment" | "case";

const ROUTE: Record<Owner, string> = { environment: "environments", case: "cases" };

export function EnvironmentFiles({ environment }: { environment: string | null }) {
  return <ReferenceFilesEditor owner="environment" name={environment} />;
}

export function CaseFiles({ name }: { name: string | null }) {
  return <ReferenceFilesEditor owner="case" name={name} />;
}

function ReferenceFilesEditor({ owner, name }: { owner: Owner; name: string | null }) {
  const client = useQueryClient();
  const input = useRef<HTMLInputElement | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [pending, setPending] = useState<File[]>([]);
  const path = name ? `/api/${ROUTE[owner]}/${encodeURIComponent(name)}/files` : "";
  const queryKey = [`${owner}-files`, name];
  const listing = useQuery({ queryKey, queryFn: () => api<ReferenceFiles>(path), enabled: Boolean(name) });
  const add = useMutation({
    mutationFn: async (files: File[]) => {
      const failed: string[] = [];
      for (const file of files) {
        try {
          await upload<ReferenceFiles>(`${path}/${encodeURIComponent(file.name)}`, file);
        } catch (error) {
          failed.push(`${file.name}: ${(error as Error).message}`);
        }
      }
      return failed;
    },
    onSuccess: (failed) => {
      setProblems(failed);
      void client.invalidateQueries({ queryKey });
    },
  });
  const drop = useMutation({
    mutationFn: (file: string) => remove<ReferenceFiles>(`${path}/${encodeURIComponent(file)}`),
    onSuccess: () => void client.invalidateQueries({ queryKey }),
    onError: (error) => setProblems([(error as Error).message]),
  });
  useEffect(() => {
    if (!name || pending.length === 0) return;
    const files = pending;
    setPending([]);
    add.mutate(files);
  }, [name, pending, add]);
  const choose = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    if (name) add.mutate(files);
    else setPending((current) => [...current.filter((file) => !files.some((chosen) => chosen.name === file.name)), ...files]);
  };
  const files = listing.data?.files ?? [];
  return (
    <fieldset className="data-editor env-files">
      <legend>Reference files (optional)</legend>
      <p className="field-help">
        {owner === "case"
          ? "Requirements, field rules or sample data the model reads while it plans and runs this case, together with its environment's files."
          : "Requirements, field rules or sample data the model reads while it plans and runs this environment's cases."}{" "}
        They are read by the local model only, never sent to the hosted decider. Text files (md, txt, pdf, docx, csv, json) are read; images (png, jpg) are kept but not yet sent to the model. Up to{" "}
        {listing.data?.max_files ?? 20} files of 25 MB each, kept in {owner === "case" ? `.hercules/case-files/${listing.data?.case ?? "<case id>"}/` : `.hercules/files/${name ?? "<environment>"}/`}.
      </p>
      {files.length || pending.length ? (
        <ul className="env-file-list">
          {files.map((file) => (
            <li key={file.name}>
              <span className="mono">{file.name}</span>
              <span className="muted small">
                {size(file.size)}
                {file.kind === "text" ? ` · ${file.chars.toLocaleString()} characters read` : " · image"}
              </span>
              <button type="button" className="icon-button" onClick={() => drop.mutate(file.name)} aria-label={`Remove ${file.name}`} disabled={drop.isPending}>
                ×
              </button>
            </li>
          ))}
          {pending.map((file) => (
            <li key={`pending-${file.name}`}>
              <span className="mono">{file.name}</span>
              <span className="muted small">
                {size(file.size)} · added when you save the {owner}
              </span>
              <button type="button" className="icon-button" onClick={() => setPending((current) => current.filter((item) => item !== file))} aria-label={`Remove ${file.name}`}>
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <label className="visually-hidden" htmlFor={`${owner}-files-input`}>
        Add reference files
      </label>
      <input ref={input} id={`${owner}-files-input`} className="visually-hidden" type="file" multiple accept={ACCEPT} onChange={choose} />
      <button type="button" className="button button-small" onClick={() => input.current?.click()} disabled={add.isPending}>
        {add.isPending ? "Adding…" : "Add files"}
      </button>
      {problems.map((problem) => (
        <p key={problem} className="field-error" role="alert">
          {problem}
        </p>
      ))}
    </fieldset>
  );
}
