import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

interface Snapshot {
  value: string;
  start: number;
  end: number;
}

const LIMIT = 200;
const TYPING_GROUP_MS = 1000;

export function useEditHistory(value: string, onChange: (value: string) => void, field: RefObject<HTMLTextAreaElement | null>, select: (range: [number, number]) => void) {
  const past = useRef<Snapshot[]>([]);
  const future = useRef<Snapshot[]>([]);
  const current = useRef<Snapshot>({ value, start: value.length, end: value.length });
  const typing = useRef(false);
  const lastWasTyping = useRef(false);
  const lastChange = useRef(0);

  useLayoutEffect(() => {
    const before = current.current;
    if (value === before.value) return;
    const now = Date.now();
    const merge = typing.current && lastWasTyping.current && now - lastChange.current < TYPING_GROUP_MS && past.current.length > 0;
    if (!merge) {
      past.current.push(before);
      if (past.current.length > LIMIT) past.current.shift();
    }
    future.current = [];
    lastWasTyping.current = typing.current;
    lastChange.current = now;
    typing.current = false;
    const element = field.current;
    current.current = { value, start: element?.selectionStart ?? value.length, end: element?.selectionEnd ?? value.length };
  }, [value, field]);

  const restore = (from: Snapshot[], to: Snapshot[]) => {
    const target = from.pop();
    if (!target) return;
    const element = field.current;
    to.push({ ...current.current, start: element?.selectionStart ?? current.current.start, end: element?.selectionEnd ?? current.current.end });
    current.current = target;
    lastWasTyping.current = false;
    select([target.start, target.end]);
    onChange(target.value);
  };
  const undo = () => restore(past.current, future.current);
  const redo = () => restore(future.current, past.current);

  useEffect(() => {
    const element = field.current;
    if (!element) return undefined;
    const onBeforeInput = (event: InputEvent) => {
      if (event.inputType !== "historyUndo" && event.inputType !== "historyRedo") return;
      event.preventDefault();
      if (event.inputType === "historyUndo") undo();
      else redo();
    };
    element.addEventListener("beforeinput", onBeforeInput);
    return () => element.removeEventListener("beforeinput", onBeforeInput);
  });

  return {
    undo,
    redo,
    typed: () => {
      typing.current = true;
    },
    remember: () => {
      const element = field.current;
      if (element && element.value === current.current.value) current.current = { value: element.value, start: element.selectionStart, end: element.selectionEnd };
    },
  };
}
