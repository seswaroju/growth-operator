import { useState, type FormEvent } from "react";

import { requestOtp, verifyOtp } from "../api";
import { useAuth } from "../auth";

export default function Login() {
  const { login } = useAuth();
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSend(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const res = await requestOtp(email);
    setBusy(false);
    if (res.ok) {
      setCode("");
      setStep("code");
    } else {
      setError(res.detail);
    }
  }

  async function onVerify(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const res = await verifyOtp(email, code);
    setBusy(false);
    if (res.ok) login(res.accessToken);
    else setError(res.detail);
  }

  const input =
    "w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 " +
    "outline-none placeholder:text-slate-500 focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400";
  const primary =
    "w-full rounded-lg bg-indigo-500 px-3 py-2 text-sm font-medium text-white " +
    "transition hover:bg-indigo-400 disabled:opacity-50";

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 p-4 text-slate-100">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-semibold tracking-tight">Growth Operator</h1>
          <p className="text-sm text-indigo-300">Operator console · staff sign-in</p>
        </div>

        <div className="rounded-2xl border border-slate-700 bg-slate-800/60 p-6 shadow-sm">
          {step === "email" ? (
            <form onSubmit={onSend} className="space-y-4">
              <label className="block text-sm font-medium text-slate-300" htmlFor="email">
                Work email
              </label>
              <input
                id="email"
                type="email"
                autoFocus
                required
                value={email}
                onChange={(ev) => setEmail(ev.target.value)}
                placeholder="you@growthoperator.com"
                className={input}
              />
              <button type="submit" disabled={busy} className={primary}>
                {busy ? "Sending…" : "Send code"}
              </button>
            </form>
          ) : (
            <form onSubmit={onVerify} className="space-y-4">
              <p className="text-sm text-slate-300">
                We sent a 6-digit code to <span className="font-medium">{email}</span>.
              </p>
              <input
                id="code"
                inputMode="numeric"
                autoFocus
                required
                value={code}
                onChange={(ev) => setCode(ev.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="123456"
                className={`${input} text-center font-mono text-lg tracking-[0.4em]`}
              />
              <button type="submit" disabled={busy || code.length < 6} className={primary}>
                {busy ? "Verifying…" : "Verify & sign in"}
              </button>
              <button
                type="button"
                onClick={() => setStep("email")}
                className="w-full text-center text-xs text-slate-400 hover:text-slate-200"
              >
                Use a different email
              </button>
            </form>
          )}

          {error && (
            <p className="mt-4 rounded-lg bg-red-950 px-3 py-2 text-xs text-red-300">{error}</p>
          )}
        </div>
        <p className="mt-3 text-center text-[11px] text-slate-500">
          Operators only. Access is granted with <span className="font-mono">make grant-admin</span>.
        </p>
      </div>
    </div>
  );
}
