import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { Link } from "wouter";
import { ApiError, patch, post, remove } from "../lib/api";
import {
  ancestors,
  caseCount,
  caseLabel,
  casesIn,
  childFolders,
  depthOf,
  folderMoveError,
  folderOptions,
  leafOf,
  MAX_FOLDER_DEPTH,
  newFolderError,
  parentOf,
  pathUrl,
  searchTree,
  type SortOrder,
} from "../lib/caseTree";
import type { CaseEntry } from "../lib/types";
import { collapseText, expandText } from "../lib/expandText";
import { Icon, type IconName } from "./Icon";
import { Modal } from "./Modal";

type Kind = "feature" | "json";

const OPEN_KEY = "hercules.cases.open";
const SORT_KEY = "hercules.cases.sort";

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    return;
  }
}

function storedOpen(): Set<string> {
  try {
    const parsed: unknown = JSON.parse(readStored(OPEN_KEY) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

export interface CaseTreeProps {
  cases: CaseEntry[];
  folders: string[];
  selected: string | null;
  collapsed: boolean;
  onCollapse: (collapsed: boolean) => void;
  onOpen: (event: MouseEvent) => void;
  onNewCase: (folder: string, kind: Kind) => void;
  onRunCase: (entry: CaseEntry) => void;
  onRunFolder: (folder: string) => void;
  onRelocate: (from: string, to: string | null) => void;
}

type MenuState = { kind: "folder"; path: string } | { kind: "case"; path: string } | null;
type EditState = { kind: "new"; parent: string; value: string; error: string | null } | { kind: "rename"; folder: string; value: string; error: string | null } | null;
type DialogState =
  | { kind: "delete-folder"; folder: string }
  | { kind: "move-folder"; folder: string }
  | { kind: "move-case"; entry: CaseEntry }
  | { kind: "add"; folder: string }
  | { kind: "delete-case"; entry: CaseEntry }
  | null;

function caseBadge(entry: CaseEntry) {
  return entry.kind === "feature" ? (
    <span className="tree-badge tree-badge-feature" aria-hidden="true">
      GK
    </span>
  ) : (
    <span className="tree-badge tree-badge-json" aria-hidden="true">
      {"{ }"}
    </span>
  );
}

function caseState(entry: CaseEntry): ReactNode {
  if (!entry.valid) return <span className="tree-dot tree-dot-bad" title={`Does not load: ${entry.error ?? ""}`} aria-label="does not load" />;
  if (entry.status === "draft") return <span className="tree-dot tree-dot-draft" title="Draft" aria-label="draft" />;
  if (entry.status === "known_bug") return <span className="tree-dot tree-dot-bug" title="Known bug" aria-label="known bug" />;
  return null;
}

function RowMenu({
  label,
  items,
  anchor,
  onClose,
}: {
  label: string;
  items: ({ label: string; icon: IconName; onSelect: () => void; danger?: boolean; hint?: string } | "sep")[];
  anchor: HTMLElement | undefined;
  onClose: (refocus: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    const menu = ref.current;
    if (!anchor || !menu) return;
    const at = anchor.getBoundingClientRect();
    const height = menu.offsetHeight;
    const below = at.bottom + 4;
    const top = below + height > window.innerHeight - 8 ? Math.max(8, at.top - height - 4) : below;
    setPlace({ top, left: Math.max(8, Math.min(at.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8)) });
  }, [anchor]);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const close = () => closeRef.current(false);
    const onPointer = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node) && !(event.target as Element).closest?.("[data-menu-trigger]")) close();
    };
    const onScroll = (event: Event) => {
      if (!ref.current?.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("resize", close);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("resize", close);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, []);
  const onKeyDown = (event: ReactKeyboardEvent) => {
    const buttons = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>("button") ?? []);
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      onClose(true);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = (at + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next]?.focus();
    }
  };
  return (
    <div className="tree-menu" role="menu" aria-label={label} ref={ref} onKeyDown={onKeyDown} style={place ?? { visibility: "hidden", top: 0, left: 0 }}>
      {items.map((item, index) =>
        item === "sep" ? (
          <hr key={`sep-${index}`} />
        ) : (
          <button
            key={item.label}
            type="button"
            role="menuitem"
            className={item.danger ? "tree-menu-danger" : undefined}
            onClick={() => {
              onClose(false);
              item.onSelect();
            }}
          >
            <Icon name={item.icon} size={14} /> {item.label}
            {item.hint ? <kbd>{item.hint}</kbd> : null}
          </button>
        ),
      )}
    </div>
  );
}

function NameRow({
  edit,
  depth,
  onChange,
  onSubmit,
  onCancel,
  pending,
}: {
  edit: NonNullable<EditState>;
  depth: number;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  pending: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);
  const label = edit.kind === "new" ? "New folder name" : "Folder name";
  return (
    <li className="tree-edit-item">
      <form
        className="tree-edit"
        style={{ ["--depth" as string]: depth }}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <Icon name="folder" size={15} />
        <input
          ref={input}
          value={edit.value}
          aria-label={label}
          aria-invalid={edit.error ? true : undefined}
          aria-describedby={edit.error ? "tree-edit-error" : undefined}
          maxLength={140}
          autoComplete="off"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              onCancel();
            }
          }}
        />
        <button type="submit" className="icon-button tree-edit-ok" aria-label={edit.kind === "new" ? "Create folder" : "Save name"} disabled={pending}>
          <Icon name="check" size={14} />
        </button>
        <button type="button" className="icon-button" aria-label="Cancel" onClick={onCancel}>
          <Icon name="cross" size={14} />
        </button>
      </form>
      {edit.error ? (
        <p id="tree-edit-error" className="tree-edit-error" role="alert" style={{ ["--depth" as string]: depth }}>
          {edit.error}
        </p>
      ) : null}
    </li>
  );
}

export function CaseTree({ cases, folders, selected, collapsed, onCollapse, onOpen, onNewCase, onRunCase, onRunFolder, onRelocate }: CaseTreeProps) {
  const client = useQueryClient();
  const [open, setOpen] = useState<Set<string>>(storedOpen);
  const [rootOpen, setRootOpen] = useState(true);
  const [order, setOrder] = useState<SortOrder>(() => (readStored(SORT_KEY) === "desc" ? "desc" : "asc"));
  const [query, setQuery] = useState("");
  const [menu, setMenu] = useState<MenuState>(null);
  const [edit, setEdit] = useState<EditState>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [target, setTarget] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const triggers = useRef(new Map<string, HTMLButtonElement>());

  const selectedFolder = selected?.includes("/") ? parentOf(selected) : "";
  useEffect(() => {
    if (!selected) return;
    setTarget(selectedFolder);
    const above = ancestors(selectedFolder);
    if (above.length) setOpen((current) => (above.every((folder) => current.has(folder)) ? current : new Set([...current, ...above])));
    setRootOpen(true);
  }, [selected, selectedFolder]);

  useEffect(() => writeStored(OPEN_KEY, JSON.stringify([...open])), [open]);
  useEffect(() => writeStored(SORT_KEY, order), [order]);
  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const refresh = () => client.invalidateQueries({ queryKey: ["cases"] });
  const reveal = (folder: string) => {
    setRootOpen(true);
    setOpen((current) => new Set([...current, ...ancestors(folder)]));
  };

  const create = useMutation({
    mutationFn: ({ parent, name }: { parent: string; name: string }) => post<{ folder: string }>("/api/folders", { parent, name }),
    onSuccess: (result) => {
      setEdit(null);
      setTarget(result.folder);
      reveal(result.folder);
      setNotice(`Created ${result.folder}`);
      void refresh();
    },
    onError: (error: Error) => setEdit((current) => (current ? { ...current, error: error.message } : current)),
  });
  const rename = useMutation({
    mutationFn: ({ folder, name }: { folder: string; name: string }) => patch<{ folder: string; was: string }>(`/api/folders/${pathUrl(folder)}`, { name }),
    onSuccess: (result) => {
      setEdit(null);
      relocateOpen(result.was, result.folder);
      onRelocate(result.was, result.folder);
      setNotice(`Renamed to ${leafOf(result.folder)}`);
      void refresh();
    },
    onError: (error: Error) => setEdit((current) => (current ? { ...current, error: error.message } : current)),
  });

  const relocateOpen = (from: string, to: string | null) => {
    setOpen((current) => {
      const next = new Set<string>();
      for (const folder of current) {
        if (folder === from || folder.startsWith(`${from}/`)) {
          if (to !== null) next.add(to ? `${to}${folder.slice(from.length)}` : folder.slice(from.length + 1));
        } else next.add(folder);
      }
      if (to) ancestors(parentOf(to)).forEach((folder) => next.add(folder));
      return next;
    });
    setTarget((current) => (current === from || current.startsWith(`${from}/`) ? (to ?? parentOf(from)) : current));
  };

  const submitEdit = () => {
    if (!edit) return;
    if (edit.kind === "new") {
      const error = newFolderError(edit.value, edit.parent, folders);
      if (error) return setEdit({ ...edit, error });
      create.mutate({ parent: edit.parent, name: edit.value.normalize("NFC").trim() });
    } else {
      const error = newFolderError(edit.value, parentOf(edit.folder), folders, edit.folder);
      if (error) return setEdit({ ...edit, error });
      if (edit.value.trim() === leafOf(edit.folder)) return setEdit(null);
      rename.mutate({ folder: edit.folder, name: edit.value.normalize("NFC").trim() });
    }
  };

  const startNew = (parent: string) => {
    setQuery("");
    setMenu(null);
    if (parent) reveal(parent);
    setRootOpen(true);
    setEdit({ kind: "new", parent, value: "", error: depthOf(parent) >= MAX_FOLDER_DEPTH ? `Folders go ${MAX_FOLDER_DEPTH} levels deep at most.` : null });
  };

  const toggle = (folder: string) => {
    setTarget(folder);
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(folder)) next.delete(folder);
      else next.add(folder);
      return next;
    });
  };

  const closeMenu = (refocus: boolean) => {
    const current = menu;
    setMenu(null);
    if (refocus && current) triggers.current.get(`${current.kind}:${current.path}`)?.focus();
  };

  const folderMenu = (folder: string) => (
    <RowMenu
      label={`Actions for folder ${leafOf(folder)}`}
      anchor={triggers.current.get(`folder:${folder}`)}
      onClose={closeMenu}
      items={[
        { label: "New case here", icon: "cases", onSelect: () => onNewCase(folder, "feature") },
        { label: "New form case here", icon: "plus", onSelect: () => onNewCase(folder, "json") },
        { label: "New folder here", icon: "folderPlus", onSelect: () => startNew(folder) },
        { label: "Add existing cases…", icon: "addIn", onSelect: () => setDialog({ kind: "add", folder }) },
        { label: "Run folder", icon: "play", onSelect: () => onRunFolder(folder) },
        "sep",
        { label: "Rename", icon: "pen", hint: "F2", onSelect: () => setEdit({ kind: "rename", folder, value: leafOf(folder), error: null }) },
        { label: "Move to…", icon: "move", onSelect: () => setDialog({ kind: "move-folder", folder }) },
        "sep",
        { label: "Delete folder", icon: "trash", hint: "Del", danger: true, onSelect: () => setDialog({ kind: "delete-folder", folder }) },
      ]}
    />
  );

  const caseMenu = (entry: CaseEntry) => (
    <RowMenu
      label={`Actions for ${caseLabel(entry)}`}
      anchor={triggers.current.get(`case:${entry.name}`)}
      onClose={closeMenu}
      items={[
        { label: "Run", icon: "play", onSelect: () => onRunCase(entry) },
        { label: "Move to…", icon: "move", onSelect: () => setDialog({ kind: "move-case", entry }) },
        "sep",
        { label: "Delete", icon: "trash", hint: "Del", danger: true, onSelect: () => setDialog({ kind: "delete-case", entry }) },
      ]}
    />
  );

  const moreButton = (kind: "folder" | "case", path: string, label: string) => {
    const on = menu?.kind === kind && menu.path === path;
    return (
      <button
        type="button"
        className="icon-button tree-more"
        aria-label={`Actions for ${label}`}
        aria-haspopup="menu"
        aria-expanded={on}
        data-menu-trigger=""
        ref={(node) => {
          if (node) triggers.current.set(`${kind}:${path}`, node);
          else triggers.current.delete(`${kind}:${path}`);
        }}
        onClick={() => setMenu(on ? null : ({ kind, path } as MenuState))}
      >
        <Icon name="more" size={15} />
      </button>
    );
  };

  const folderRow = (folder: string, depth: number, hint?: string) => {
    if (edit?.kind === "rename" && edit.folder === folder)
      return (
        <NameRow
          key={`edit-${folder}`}
          edit={edit}
          depth={depth}
          pending={rename.isPending}
          onChange={(value) => setEdit({ ...edit, value, error: null })}
          onSubmit={submitEdit}
          onCancel={() => setEdit(null)}
        />
      );
    const isOpen = !hint && open.has(folder);
    const count = caseCount(cases, folder);
    return (
      <li key={`folder-${folder}`}>
        <div className={`tree-row${target === folder && !selected?.startsWith(`${folder}/`) ? " tree-row-target" : ""}`} style={{ ["--depth" as string]: depth }}>
          <button
            type="button"
            className="tree-main"
            aria-expanded={hint ? undefined : isOpen}
            onClick={() => {
              if (hint) {
                setQuery("");
                reveal(folder);
                setTarget(folder);
              } else toggle(folder);
            }}
            onKeyDown={(event) => {
              if (event.key === "F2") {
                event.preventDefault();
                setEdit({ kind: "rename", folder, value: leafOf(folder), error: null });
              } else if (event.key === "Delete") {
                event.preventDefault();
                setDialog({ kind: "delete-folder", folder });
              }
            }}
          >
            <span className={`tree-twisty${isOpen ? " tree-twisty-open" : ""}`} aria-hidden="true">
              {hint ? null : <Icon name="chevron" size={12} />}
            </span>
            <Icon name={isOpen ? "folderOpen" : "folder"} size={15} />
            <span className="tree-name">
              {leafOf(folder)}
              {hint !== undefined ? <small className="tree-hint">{hint || "top level"}</small> : null}
            </span>
            <span className="tree-count">
              {count ? (
                <>
                  {count}
                  {" "}
                  <span className="visually-hidden">{count === 1 ? "case" : "cases"}</span>
                </>
              ) : null}
            </span>
          </button>
          {moreButton("folder", folder, `folder ${leafOf(folder)}`)}
          {menu?.kind === "folder" && menu.path === folder ? folderMenu(folder) : null}
        </div>
        {hint === undefined ? (
          <div className={`tree-collapse${isOpen ? "" : " tree-collapse-closed"}`} inert={!isOpen}>
            <ul>{branch(folder, depth + 1)}</ul>
          </div>
        ) : null}
      </li>
    );
  };

  const caseRow = (entry: CaseEntry, depth: number, hint?: string) => {
    const on = entry.name === selected;
    return (
      <li key={`case-${entry.name}`}>
        <div
          className={`tree-row${on ? " tree-row-on" : ""}`}
          style={{ ["--depth" as string]: depth }}
          onPointerEnter={(event) => expandText(event.currentTarget, ".tree-name")}
          onPointerLeave={(event) => collapseText(event.currentTarget, ".tree-name")}
          onFocus={(event) => {
            if (event.target.matches(":focus-visible")) expandText(event.currentTarget, ".tree-name");
          }}
          onBlur={(event) => {
            const row = event.currentTarget;
            window.setTimeout(() => collapseText(row, ".tree-name"));
          }}
        >
          <Link
            href={`/cases/${pathUrl(entry.name)}`}
            className="tree-main"
            title={entry.name}
            aria-current={on ? "page" : undefined}
            onClick={(event: MouseEvent) => {
              setQuery("");
              onOpen(event);
            }}
            onKeyDown={(event: ReactKeyboardEvent) => {
              if (event.key === "Delete") {
                event.preventDefault();
                setDialog({ kind: "delete-case", entry });
              }
            }}
          >
            <span className="tree-twisty" aria-hidden="true" />
            {caseBadge(entry)}
            <span className="tree-name">
              {caseLabel(entry)}
              {hint !== undefined ? <small className="tree-hint">{hint || "top level"}</small> : null}
            </span>
            <span className="tree-count">{caseState(entry)}</span>
          </Link>
          {moreButton("case", entry.name, caseLabel(entry))}
          {menu?.kind === "case" && menu.path === entry.name ? caseMenu(entry) : null}
        </div>
      </li>
    );
  };

  function branch(parent: string, depth: number): ReactNode[] {
    const rows: ReactNode[] = [];
    if (edit?.kind === "new" && edit.parent === parent)
      rows.push(
        <NameRow
          key="edit-new"
          edit={edit}
          depth={depth}
          pending={create.isPending}
          onChange={(value) => setEdit({ ...edit, value, error: null })}
          onSubmit={submitEdit}
          onCancel={() => setEdit(null)}
        />,
      );
    for (const folder of childFolders(folders, parent, order)) rows.push(folderRow(folder, depth));
    for (const entry of casesIn(cases, parent, order)) rows.push(caseRow(entry, depth));
    return rows;
  }

  const hits = useMemo(() => searchTree(cases, folders, query, order), [cases, folders, query, order]);

  if (collapsed) {
    return (
      <nav className="panel case-tree case-tree-rail" aria-label="Saved cases">
        <button type="button" className="icon-button" onClick={() => onCollapse(false)} aria-label="Show cases" title="Show cases">
          <Icon name="panelShow" size={16} />
        </button>
      </nav>
    );
  }

  return (
    <nav className="panel case-tree" aria-label="Saved cases">
      <div className="case-tree-head">
        <button type="button" className="link-button case-tree-new" onClick={() => startNew(target)} title={`New folder in ${target || "the top level"}`}>
          <Icon name="folderPlus" size={15} /> New
        </button>
        <button type="button" className="icon-button" onClick={() => onCollapse(true)} aria-label="Hide cases" title="Hide cases">
          <Icon name="panelHide" size={16} />
        </button>
      </div>
      <div className="case-tree-tools">
        <label className="search case-tree-search">
          <Icon name="search" size={14} />
          <span className="visually-hidden">Search cases and folders</span>
          <input type="search" placeholder="Search cases and folders…" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
        <button
          type="button"
          className="icon-button case-tree-sort"
          onClick={() => setOrder(order === "asc" ? "desc" : "asc")}
          aria-label={`Sort ${order === "asc" ? "Z to A" : "A to Z"}`}
          title={order === "asc" ? "A–Z" : "Z–A"}
        >
          <Icon name="sort" size={15} />
        </button>
      </div>
      {notice ? (
        <p className="case-tree-notice" role="status">
          {notice}
        </p>
      ) : null}
      {query.trim() ? (
        <ul className="case-tree-list" aria-label="Search results">
          {hits.folders.map((folder) => folderRow(folder, 0, parentOf(folder)))}
          {hits.cases.map((entry) => caseRow(entry, 0, entry.folder ?? ""))}
          {hits.folders.length + hits.cases.length === 0 ? <li className="muted small case-tree-empty">Nothing matches “{query.trim()}”.</li> : null}
        </ul>
      ) : (
        <ul className="case-tree-list">
          <li>
            <div className="tree-row tree-row-root" style={{ ["--depth" as string]: 0 }}>
              <button type="button" className="tree-main" aria-expanded={rootOpen} onClick={() => setRootOpen(!rootOpen)}>
                <span className={`tree-twisty${rootOpen ? " tree-twisty-open" : ""}`} aria-hidden="true">
                  <Icon name="chevron" size={12} />
                </span>
                <Icon name="layers" size={15} />
                <span className="tree-name">All cases</span>
                <span className="tree-count">{cases.length}</span>
              </button>
            </div>
          </li>
          <li className="case-tree-sep" role="none" />
          <li className={`tree-collapse${rootOpen ? "" : " tree-collapse-closed"}`} inert={!rootOpen}>
            <ul>
              {branch("", 0)}
              {cases.length === 0 && folders.length === 0 && !edit ? <li className="muted small case-tree-empty">No cases in .hercules/cases yet.</li> : null}
            </ul>
          </li>
        </ul>
      )}
      {dialog ? (
        <TreeDialog
          dialog={dialog}
          cases={cases}
          folders={folders}
          onClose={() => setDialog(null)}
          onDone={(message, from, to) => {
            setDialog(null);
            setNotice(message);
            if (from !== undefined) {
              relocateOpen(from, to ?? null);
              onRelocate(from, to ?? null);
            }
            if (to) reveal(dialog.kind === "move-case" || dialog.kind === "move-folder" ? parentOf(to) : to);
            void refresh();
          }}
        />
      ) : null}
    </nav>
  );
}

function TreeDialog({
  dialog,
  cases,
  folders,
  onClose,
  onDone,
}: {
  dialog: NonNullable<DialogState>;
  cases: CaseEntry[];
  folders: string[];
  onClose: () => void;
  onDone: (message: string, from?: string, to?: string | null) => void;
}) {
  const [choice, setChoice] = useState<string | null>(dialog.kind === "delete-folder" ? "move_up" : null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const action = useMutation({
    mutationFn: async () => {
      if (dialog.kind === "delete-folder") {
        await remove(`/api/folders/${pathUrl(dialog.folder)}?mode=${choice}`);
        const n = caseCount(cases, dialog.folder);
        return choice === "delete_cases"
          ? { message: `Deleted ${leafOf(dialog.folder)} and ${n} ${n === 1 ? "case" : "cases"}`, from: dialog.folder, to: null }
          : { message: `Deleted ${leafOf(dialog.folder)}; what it held is now in ${parentOf(dialog.folder) || "the top level"}`, from: dialog.folder, to: parentOf(dialog.folder) };
      }
      if (dialog.kind === "move-folder") {
        const result = await patch<{ folder: string; was: string }>(`/api/folders/${pathUrl(dialog.folder)}`, { parent: choice ?? "" });
        return { message: `Moved ${leafOf(dialog.folder)} to ${choice || "the top level"}`, from: result.was, to: result.folder };
      }
      if (dialog.kind === "move-case") {
        const result = await post<{ names: string[] }>("/api/cases/move", { names: [dialog.entry.name], folder: choice ?? "" });
        return { message: `Moved ${caseLabel(dialog.entry)} to ${choice || "the top level"}`, from: dialog.entry.name, to: result.names[0] ?? dialog.entry.name };
      }
      if (dialog.kind === "add") {
        const names = [...picked];
        await post("/api/cases/move", { names, folder: dialog.folder });
        return { message: `Moved ${names.length} ${names.length === 1 ? "case" : "cases"} into ${leafOf(dialog.folder)}`, moved: names };
      }
      await remove(`/api/cases/${pathUrl(dialog.entry.name)}`);
      return { message: `Deleted ${caseLabel(dialog.entry)}`, from: dialog.entry.name, to: null };
    },
    onSuccess: (result) => {
      if ("moved" in result && dialog.kind === "add") {
        onDone(result.message, undefined, dialog.folder);
        return;
      }
      if ("from" in result) onDone(result.message, result.from, result.to);
    },
  });
  const error = action.error instanceof ApiError || action.error instanceof Error ? action.error.message : null;
  const footer = (submit: string, disabled: boolean, danger = false) => (
    <>
      {error ? (
        <p className="callout callout-fail" role="alert">
          {error}
        </p>
      ) : null}
      <footer className="form-actions">
        <button type="button" className="button button-ghost" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className={`button ${danger ? "button-danger" : "button-primary"}`} disabled={disabled || action.isPending}>
          {action.isPending ? "Working…" : submit}
        </button>
      </footer>
    </>
  );
  const destinations = (why: (path: string) => string | null) => (
    <fieldset className="tree-pick">
      <legend className="visually-hidden">Destination</legend>
      {[{ path: "", depth: 0 }, ...folderOptions(folders)].map((option) => {
        const reason = why(option.path);
        return (
          <label key={option.path || "(top)"} className={reason ? "tree-pick-off" : undefined} style={{ ["--depth" as string]: option.depth }}>
            <input type="radio" name="destination" value={option.path} checked={choice === option.path} disabled={Boolean(reason)} onChange={() => setChoice(option.path)} />
            <span>{option.path ? leafOf(option.path) : "Top level"}</span>
            {reason ? <small>{reason}</small> : null}
          </label>
        );
      })}
    </fieldset>
  );
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    action.mutate();
  };

  if (dialog.kind === "delete-folder") {
    const n = caseCount(cases, dialog.folder);
    const subs = folders.filter((folder) => folder.startsWith(`${dialog.folder}/`)).length;
    const parent = parentOf(dialog.folder);
    return (
      <Modal title={`Delete “${leafOf(dialog.folder)}”`} onClose={onClose}>
        <form className="form" onSubmit={onSubmit}>
          <p>
            It holds {n} {n === 1 ? "case" : "cases"}
            {subs ? ` and ${subs} ${subs === 1 ? "subfolder" : "subfolders"}` : ""}.
          </p>
          <fieldset className="tree-choices">
            <legend className="visually-hidden">What happens to what it holds</legend>
            <label>
              <input type="radio" name="mode" checked={choice === "move_up"} onChange={() => setChoice("move_up")} />
              <span>Keep the cases</span>
              <small>Move what it holds into {parent ? leafOf(parent) : "the top level"}.</small>
            </label>
            <label className={n ? undefined : "tree-pick-off"}>
              <input type="radio" name="mode" checked={choice === "delete_cases"} disabled={!n} onChange={() => setChoice("delete_cases")} />
              <span>Delete the cases too</span>
              <small>{n ? `Removes ${n} case ${n === 1 ? "file" : "files"} with their test data and files for upload steps. This cannot be undone.` : "There are no cases to delete."}</small>
            </label>
          </fieldset>
          {footer("Delete folder", false, true)}
        </form>
      </Modal>
    );
  }
  if (dialog.kind === "move-folder") {
    return (
      <Modal title={`Move “${leafOf(dialog.folder)}”`} onClose={onClose}>
        <form className="form" onSubmit={onSubmit}>
          <p>Its cases and subfolders move with it.</p>
          {destinations((path) => folderMoveError(dialog.folder, path, folders))}
          {footer("Move here", choice === null)}
        </form>
      </Modal>
    );
  }
  if (dialog.kind === "move-case") {
    const current = dialog.entry.folder ?? "";
    return (
      <Modal title={`Move “${caseLabel(dialog.entry)}”`} onClose={onClose}>
        <form className="form" onSubmit={onSubmit}>
          <p>Its test data, files for upload steps and past runs stay linked.</p>
          {destinations((path) => (path === current ? "It is here now" : null))}
          {footer("Move here", choice === null)}
        </form>
      </Modal>
    );
  }
  if (dialog.kind === "add") {
    const others = cases.filter((entry) => (entry.folder ?? "") !== dialog.folder);
    return (
      <Modal title={`Add cases to “${leafOf(dialog.folder)}”`} onClose={onClose}>
        <form className="form" onSubmit={onSubmit}>
          <p>The chosen case files move into this folder.</p>
          {others.length ? (
            <fieldset className="tree-pick">
              <legend className="visually-hidden">Cases</legend>
              {others.map((entry) => (
                <label key={entry.name}>
                  <input
                    type="checkbox"
                    checked={picked.has(entry.name)}
                    onChange={(event) =>
                      setPicked((current) => {
                        const next = new Set(current);
                        if (event.target.checked) next.add(entry.name);
                        else next.delete(entry.name);
                        return next;
                      })
                    }
                  />
                  <span>{caseLabel(entry)}</span>
                  <small>{entry.folder ? `${entry.folder}/${leafOf(entry.name)}` : leafOf(entry.name)}</small>
                </label>
              ))}
            </fieldset>
          ) : (
            <p className="muted">Every case is already in this folder.</p>
          )}
          {footer(picked.size ? `Move ${picked.size} ${picked.size === 1 ? "case" : "cases"} here` : "Move here", picked.size === 0)}
        </form>
      </Modal>
    );
  }
  return (
    <Modal title={`Delete “${caseLabel(dialog.entry)}”`} onClose={onClose}>
      <form className="form" onSubmit={onSubmit}>
        <p>{leafOf(dialog.entry.name)}, its test data and its files for upload steps are deleted. Its past runs stay on the Runs screen. This cannot be undone.</p>
        {footer("Delete case", false, true)}
      </form>
    </Modal>
  );
}
