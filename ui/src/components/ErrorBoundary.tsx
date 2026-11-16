import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** A change of this value (the location) clears the error, so navigating away recovers. */
  resetKey?: string;
}

interface State {
  error: Error | null;
  resetKey: string | undefined;
}

/** Catches a render error of a screen and offers a reload instead of a blank page. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Kept in the browser console for a bug report; nothing leaves the machine.
    console.error("Hercules UI crashed", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <section className="crash" role="alert" aria-labelledby="crash-title">
        <h1 id="crash-title">This screen stopped working</h1>
        <p>Something in the page went wrong while showing it. Your runs and cases are not affected: they live in the daemon and in .hercules/.</p>
        <p className="crash-detail">{error.message || error.name}</p>
        <div className="crash-actions">
          <button type="button" className="button button-primary" onClick={() => window.location.reload()}>
            Reload the page
          </button>
          <a className="button button-ghost" href="/">
            Back to runs
          </a>
        </div>
      </section>
    );
  }
}
