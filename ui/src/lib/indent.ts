export interface IndentEdit {
  from: number;
  to: number;
  text: string;
  selectionStart: number;
  selectionEnd: number;
}

const INDENT = "  ";

export function indentEdit(value: string, start: number, end: number, outdent: boolean): IndentEdit | null {
  if (!outdent && start === end) return { from: start, to: end, text: INDENT, selectionStart: start + INDENT.length, selectionEnd: start + INDENT.length };
  const lineStart = value.lastIndexOf("\n", start - 1) + 1;
  const lastIncluded = end > start && value[end - 1] === "\n" ? end - 1 : end;
  const found = value.indexOf("\n", lastIncluded);
  const lineEnd = found === -1 ? value.length : found;
  const block = value.slice(lineStart, lineEnd);
  const lines = block.split("\n");
  const changed = outdent ? lines.map((line) => line.replace(/^( {1,2}|\t)/, "")) : lines.map((line) => INDENT + line);
  const text = changed.join("\n");
  if (text === block) return null;
  const firstShift = changed[0].length - lines[0].length;
  const selectionStart = Math.max(lineStart, start + firstShift);
  const selectionEnd = start === end ? selectionStart : end + text.length - block.length;
  return { from: lineStart, to: lineEnd, text, selectionStart, selectionEnd };
}

export function moveLinesEdit(value: string, start: number, end: number, direction: "up" | "down"): IndentEdit | null {
  const lineStart = value.lastIndexOf("\n", start - 1) + 1;
  const lastIncluded = end > start && value[end - 1] === "\n" ? end - 1 : end;
  const found = value.indexOf("\n", lastIncluded);
  const lineEnd = found === -1 ? value.length : found;
  const block = value.slice(lineStart, lineEnd);
  if (direction === "up") {
    if (lineStart === 0) return null;
    const previousStart = value.lastIndexOf("\n", lineStart - 2) + 1;
    const previous = value.slice(previousStart, lineStart - 1);
    const shift = -(previous.length + 1);
    return { from: previousStart, to: lineEnd, text: `${block}\n${previous}`, selectionStart: start + shift, selectionEnd: end + shift };
  }
  if (lineEnd === value.length) return null;
  const nextFound = value.indexOf("\n", lineEnd + 1);
  const nextEnd = nextFound === -1 ? value.length : nextFound;
  const next = value.slice(lineEnd + 1, nextEnd);
  const shift = next.length + 1;
  return { from: lineStart, to: nextEnd, text: `${next}\n${block}`, selectionStart: start + shift, selectionEnd: end + shift };
}
