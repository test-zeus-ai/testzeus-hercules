import type { CaseEntry } from "./types";

export const MAX_FOLDER_DEPTH = 8;
export const MAX_FOLDER_NAME = 120;
const FORBIDDEN = /[\\/:*?"<>|\u0000-\u001f\u007f]/;
const RESERVED = new Set(["con", "prn", "aux", "nul", ...Array.from({ length: 9 }, (_, i) => `com${i + 1}`), ...Array.from({ length: 9 }, (_, i) => `lpt${i + 1}`)]);

export const parentOf = (folder: string): string => (folder.includes("/") ? folder.slice(0, folder.lastIndexOf("/")) : "");
export const leafOf = (path: string): string => path.slice(path.lastIndexOf("/") + 1);
export const depthOf = (folder: string): number => (folder ? folder.split("/").length : 0);
export const joinPath = (parent: string, name: string): string => (parent ? `${parent}/${name}` : name);
export const within = (path: string, folder: string): boolean => folder === "" || path === folder || path.startsWith(`${folder}/`);

export function pathUrl(path: string): string {
  return path
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

export function folderNameError(raw: string): string | null {
  const name = raw.normalize("NFC").trim();
  if (!name) return "Enter a name.";
  if (name.length > MAX_FOLDER_NAME) return `Use ${MAX_FOLDER_NAME} characters or fewer.`;
  if (FORBIDDEN.test(name)) return 'A folder name can’t contain / \\ : * ? " < > |';
  if (name.startsWith(".")) return "A folder name can’t start with a dot.";
  if (name.endsWith(".")) return "A folder name can’t end with a dot.";
  if (RESERVED.has(name.split(".")[0].toLowerCase())) return `${name} is a reserved name on Windows.`;
  return null;
}

export function newFolderError(raw: string, parent: string, folders: readonly string[], except?: string): string | null {
  const shape = folderNameError(raw);
  if (shape) return shape;
  if (depthOf(parent) + 1 > MAX_FOLDER_DEPTH) return `Folders go ${MAX_FOLDER_DEPTH} levels deep at most.`;
  const name = raw.normalize("NFC").trim().toLowerCase();
  const clash = folders.find((folder) => folder !== except && parentOf(folder) === parent && leafOf(folder).toLowerCase() === name);
  return clash ? `“${leafOf(clash)}” is already in ${parent ? leafOf(parent) : "the top level"}.` : null;
}

export function folderMoveError(folder: string, target: string, folders: readonly string[]): string | null {
  if (target === folder) return "This folder";
  if (target.startsWith(`${folder}/`)) return "Inside this folder";
  if (target === parentOf(folder)) return "It is here now";
  const height = 1 + Math.max(0, ...folders.filter((other) => other.startsWith(`${folder}/`)).map((other) => depthOf(other) - depthOf(folder)));
  if (depthOf(target) + height > MAX_FOLDER_DEPTH) return "Too deep";
  const name = leafOf(folder).toLowerCase();
  if (folders.some((other) => parentOf(other) === target && leafOf(other).toLowerCase() === name)) return `Has a folder named ${leafOf(folder)}`;
  return null;
}

export type SortOrder = "asc" | "desc";

const compare = (order: SortOrder) => (a: string, b: string) => (order === "asc" ? 1 : -1) * a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });

export function childFolders(folders: readonly string[], parent: string, order: SortOrder): string[] {
  return folders.filter((folder) => parentOf(folder) === parent).sort((a, b) => compare(order)(leafOf(a), leafOf(b)));
}

export const caseLabel = (entry: CaseEntry): string => entry.title?.trim() || leafOf(entry.name);

export function casesIn(cases: readonly CaseEntry[], folder: string, order: SortOrder): CaseEntry[] {
  return cases.filter((entry) => (entry.folder ?? "") === folder).sort((a, b) => compare(order)(caseLabel(a), caseLabel(b)));
}

export function caseCount(cases: readonly CaseEntry[], folder: string): number {
  return cases.filter((entry) => entry.folder && within(entry.folder, folder)).length;
}

export function ancestors(folder: string): string[] {
  const found: string[] = [];
  let current = folder;
  while (current) {
    found.push(current);
    current = parentOf(current);
  }
  return found;
}

export interface SearchHits {
  folders: string[];
  cases: CaseEntry[];
}

export function searchTree(cases: readonly CaseEntry[], folders: readonly string[], query: string, order: SortOrder): SearchHits {
  const needle = query.trim().toLowerCase();
  if (!needle) return { folders: [], cases: [] };
  return {
    folders: folders.filter((folder) => leafOf(folder).toLowerCase().includes(needle)).sort((a, b) => compare(order)(leafOf(a), leafOf(b))),
    cases: cases.filter((entry) => leafOf(entry.name).toLowerCase().includes(needle) || (entry.title ?? "").toLowerCase().includes(needle)).sort((a, b) => compare(order)(caseLabel(a), caseLabel(b))),
  };
}

export function folderOptions(folders: readonly string[]): { path: string; depth: number }[] {
  const found: { path: string; depth: number }[] = [];
  const walk = (parent: string) => {
    for (const folder of childFolders(folders, parent, "asc")) {
      found.push({ path: folder, depth: depthOf(folder) });
      walk(folder);
    }
  };
  walk("");
  return found;
}

export function relocated(name: string, from: string, to: string | null): string | null {
  if (name !== from && !name.startsWith(`${from}/`)) return name;
  if (to === null) return null;
  const rest = name.slice(from.length);
  return to ? `${to}${rest}` : rest.replace(/^\//, "");
}
