import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";

import "./index.css";
import { AuthProvider, useAuth } from "./auth";
import Login from "./components/Login";
import PlaneDisabled from "./components/PlaneDisabled";
import { router } from "./router";

const queryClient = new QueryClient();

function Root() {
  const { status } = useAuth();
  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-900 text-sm text-slate-400">
        Loading…
      </div>
    );
  }
  if (status === "anon") return <Login />;
  if (status === "authed") return <RouterProvider router={router} />;
  return <PlaneDisabled reason={status} />; // disabled | forbidden | unreachable
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
