import { Component, type ErrorInfo, type ReactNode } from "react";

import { reportError } from "../lib/errorTracking";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

// App-wide error boundary: catches a render/runtime crash so the store owner sees a calm recovery
// screen instead of a blank page, and reports the error to GlitchTip (a no-op when tracking isn't
// configured — see lib/errorTracking). React error boundaries must be class components.
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportError(error, { componentStack: info.componentStack });
  }

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-porcelain p-4 text-center text-ink">
        <h1 className="font-serif text-xl font-medium tracking-tight">Something went wrong</h1>
        <p className="max-w-sm text-sm text-ink-2">
          We hit an unexpected error and have been notified. Reloading usually fixes it.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-white shadow-card hover:bg-accent-2"
        >
          Reload
        </button>
      </div>
    );
  }
}
