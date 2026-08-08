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
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-neutral-50 p-4 text-center text-neutral-900">
        <h1 className="text-lg font-semibold tracking-tight">Something went wrong</h1>
        <p className="max-w-sm text-sm text-neutral-600">
          We hit an unexpected error and have been notified. Reloading usually fixes it.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="rounded-lg bg-neutral-900 px-4 py-1.5 text-sm font-medium text-white"
        >
          Reload
        </button>
      </div>
    );
  }
}
