import { useEffect, useRef, type ReactNode } from "react";

/** A modal built on <dialog>: focus is trapped by the browser, Escape closes it, focus returns afterwards. */
export function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const pressedBackdrop = useRef(false);
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    if (dialog && typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    else dialog?.setAttribute("open", "");
    return () => {
      if (dialog && typeof dialog.close === "function" && dialog.open) dialog.close();
      previous?.focus?.();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal${wide ? " modal-wide" : ""}`}
      aria-labelledby="modal-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      // Close on a click on the backdrop only when the press started there too: a text selection
      // dragged out of the dialog ends on the backdrop and must not close it.
      onPointerDown={(event) => {
        pressedBackdrop.current = event.target === ref.current;
      }}
      onClick={(event) => {
        const started = pressedBackdrop.current;
        pressedBackdrop.current = false;
        if (started && event.target === ref.current) onClose();
      }}
    >
      <div className="modal-body">
        <header className="modal-head">
          <h2 id="modal-title">{title}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
