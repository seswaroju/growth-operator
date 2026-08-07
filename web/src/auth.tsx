import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { getMe, type Me } from "./api";

const TOKEN_KEY = "go_customer_access_token";

interface AuthState {
  token: string | null;
  me: Me | null;
  loading: boolean;
  error: string | null;
  login: (accessToken: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState<boolean>(() => localStorage.getItem(TOKEN_KEY) !== null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setMe(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getMe(token)
      .then((m) => {
        if (!cancelled) {
          setMe(m);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          // Token invalid/expired → drop it and fall back to the login screen.
          setMe(null);
          setError((e as Error).message);
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
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
  }

  return (
    <AuthContext.Provider value={{ token, me, loading, error, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
