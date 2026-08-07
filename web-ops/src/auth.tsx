import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { ApiError, getAdminMe, type AdminMe } from "./api";

const TOKEN_KEY = "go_operator_access_token"; // separate from the customer app's token

export type OpStatus =
  | "loading"
  | "anon" // no token → show login
  | "authed" // valid operator → show console
  | "forbidden" // signed in, but not on the operator allowlist
  | "disabled" // the operator plane is switched off (backend 404)
  | "unreachable"; // backend not reachable

interface AuthState {
  token: string | null;
  me: AdminMe | null;
  status: OpStatus;
  login: (accessToken: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [me, setMe] = useState<AdminMe | null>(null);
  const [status, setStatus] = useState<OpStatus>(() =>
    localStorage.getItem(TOKEN_KEY) ? "loading" : "anon",
  );

  useEffect(() => {
    if (!token) {
      setMe(null);
      setStatus("anon");
      return;
    }
    let cancelled = false;
    setStatus("loading");
    getAdminMe(token)
      .then((m) => {
        if (!cancelled) {
          setMe(m);
          setStatus("authed");
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setMe(null);
        const status = (e as ApiError).status;
        if (status === 404) setStatus("disabled");
        else if (status === 403) setStatus("forbidden");
        else if (status === 401) {
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
          setStatus("anon");
        } else setStatus("unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  function login(accessToken: string) {
    localStorage.setItem(TOKEN_KEY, accessToken);
    setToken(accessToken);
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setMe(null);
    setStatus("anon");
  }

  return (
    <AuthContext.Provider value={{ token, me, status, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
