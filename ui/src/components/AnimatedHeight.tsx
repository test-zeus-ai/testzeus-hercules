import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

/**
 * Animates its height to fit its content whenever the content's size changes, so a dialog grows and shrinks
 * smoothly; a change of ``swapKey`` also fades the new content in. Without ResizeObserver it is a plain block.
 */
export function AnimatedHeight({ swapKey, children }: { swapKey: string; children: ReactNode }) {
  const inner = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(undefined);
  useLayoutEffect(() => {
    const element = inner.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const measure = () => setHeight(element.offsetHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return (
    <div className="animated-height" style={height === undefined ? undefined : { height }}>
      <div ref={inner}>
        <div key={swapKey} className="animated-swap">
          {children}
        </div>
      </div>
    </div>
  );
}
