export type TokenKind = "keyword" | "env" | "data" | "input" | "output" | "builtin" | "unknown";

export interface Token {
  text: string;
  kind?: TokenKind;
}

const TOKEN = /\{\{\s*([A-Za-z0-9_]*)\s*\}\}|@env:[A-Za-z0-9_-]*|\[\$\.(environment|testdata|input|output)\.[^\]\s]*\]/g;
const KEYWORD = /^(\s*)(Feature:|Background:|Scenario Outline:|Scenario Template:|Scenario:|Examples:|Example:|Scenarios:|Rule:|Given(?=\s|$)|When(?=\s|$)|Then(?=\s|$)|And(?=\s|$)|But(?=\s|$)|\*(?=\s|$))/;
const DOC_FENCE = /^\s*("""|```)/;
const REFERENCE_KIND: Record<string, TokenKind> = { environment: "env", testdata: "data", input: "input", output: "output" };

function inlineTokens(text: string, kinds: ReadonlyMap<string, "data" | "env" | "builtin">, tokens: Token[]): void {
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    if (match.index > last) tokens.push({ text: text.slice(last, match.index) });
    const kind: TokenKind = match[0].startsWith("@env:") ? "input" : match[2] ? REFERENCE_KIND[match[2]] : (kinds.get(match[1]) ?? "unknown");
    tokens.push({ text: match[0], kind });
    last = match.index + match[0].length;
  }
  if (last < text.length) tokens.push({ text: text.slice(last) });
}

export function gherkinTokens(text: string, kinds: ReadonlyMap<string, "data" | "env" | "builtin">): Token[] {
  const tokens: Token[] = [];
  let fenced = false;
  text.split("\n").forEach((line, offset) => {
    if (offset > 0) tokens.push({ text: "\n" });
    if (DOC_FENCE.test(line)) {
      fenced = !fenced;
      tokens.push({ text: line });
      return;
    }
    const keyword = fenced ? null : KEYWORD.exec(line);
    if (keyword) {
      if (keyword[1]) tokens.push({ text: keyword[1] });
      tokens.push({ text: keyword[2], kind: "keyword" });
    }
    inlineTokens(keyword ? line.slice(keyword[0].length) : line, kinds, tokens);
  });
  return tokens;
}
