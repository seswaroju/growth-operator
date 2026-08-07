import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";

import "./index.css";
import { AuthProvider, useAuth } from "./auth";
import Login from "./components/Login";
import { router } from "./router";

const queryClient = new QueryClient();

function Root() {
  const { me, loading, logout } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-neutral-50 text-sm text-neutral-500">
        Loading…
      </div>
    );
  }
  if (!me) return <Login />;
  if (!me.org) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-neutral-50 p-4 text-center text-neutral-900">
        <p className="text-sm text-neutral-700">You're signed in, but not part of any store yet.</p>
        <button
          onClick={logout}
          className="rounded-lg border border-neutral-300 px-3 py-1.5 text-xs font-medium hover:bg-neutral-50"
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
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Root />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
