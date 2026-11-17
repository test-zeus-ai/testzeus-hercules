export const TEXT_MOTION_MS = 450;

const timers = new WeakMap<HTMLElement, number>();

function textIn(holder: Element, selector: string): HTMLElement | null {
  return holder.matches(selector) ? (holder as HTMLElement) : holder.querySelector<HTMLElement>(selector);
}

export function expandText(holder: Element, selector: string): void {
  const text = textIn(holder, selector);
  if (!text) return;
  window.clearTimeout(timers.get(text));
  if (!text.classList.contains("text-full")) {
    if (text.scrollWidth <= text.clientWidth + 1) return;
    text.dataset.line = String(text.offsetHeight);
    text.style.height = `${text.offsetHeight}px`;
    text.classList.add("text-full");
  }
  text.style.height = `${text.scrollHeight}px`;
  timers.set(
    text,
    window.setTimeout(() => {
      text.style.height = "";
    }, TEXT_MOTION_MS),
  );
}

export function collapseText(holder: Element, selector: string): void {
  const text = textIn(holder, selector);
  if (!text || !text.classList.contains("text-full") || holder.matches(":hover") || holder.querySelector(":focus-visible")) return;
  window.clearTimeout(timers.get(text));
  text.style.height = `${text.offsetHeight}px`;
  void text.offsetHeight;
  text.style.height = `${text.dataset.line ?? text.offsetHeight}px`;
  timers.set(
    text,
    window.setTimeout(() => {
      text.classList.remove("text-full");
      text.style.height = "";
    }, TEXT_MOTION_MS),
  );
}
