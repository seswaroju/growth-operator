import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";

import "./index.css";
import { AuthProvider, useAuth } from "./auth";
import ErrorBoundary from "./components/ErrorBoundary";
import Login from "./components/Login";
import { initErrorTracking } from "./lib/errorTracking";
import { router } from "./router";

// Error tracking is inert unless VITE_ERROR_DSN is configured (security S2).
initErrorTracking();

const queryClient = new QueryClient();

function Root() {
  const { me, loading, logout } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-porcelain text-sm text-muted">
        Loading…
      </div>
    );
  }
  if (!me) return <Login />;
  if (!me.org) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-porcelain p-4 text-center text-ink">
        <p className="text-sm text-ink-2">You're signed in, but not part of any store yet.</p>
        <button
          onClick={logout}
          className="rounded-xl border border-line bg-surface px-3 py-1.5 text-xs font-medium hover:border-muted"
        >
          Sign out
        </button>
      </div>
    );
  }
  return <RouterProvider router={router} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <Root />
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
