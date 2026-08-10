import { useState, type FormEvent } from "react";

import { requestOtp, verifyOtp } from "../api";
import { useAuth } from "../auth";
import { ArrowRight, Mark } from "./icons";

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
    "w-full rounded-xl border border-line bg-raised px-3.5 py-3 text-sm text-ink caret-accent " +
    "outline-none placeholder:text-muted focus:border-accent focus:ring-4 focus:ring-accent-soft";
  const primary =
    "flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-3 py-3 text-sm " +
    "font-semibold text-on-accent shadow-card transition hover:bg-accent-2 disabled:opacity-50";

  return (
    <div className="flex min-h-screen items-center justify-center bg-porcelain p-4 text-ink">
      <div className="w-full max-w-sm">
        <div className="rounded-2xl border border-line bg-surface p-7 shadow-pop">
          <div className="mx-auto mb-4 grid h-11 w-11 place-items-center rounded-xl bg-ink text-porcelain">
            <Mark className="h-6 w-6" />
          </div>
          <h1 className="text-center font-serif text-[22px] font-medium tracking-tight">Operator console</h1>
          <p className="mb-6 mt-1 text-center text-sm text-muted">
            {step === "email" ? "Staff sign-in" : "Enter the code we sent"}
          </p>

          {step === "email" ? (
            <form onSubmit={onSend} className="space-y-3.5">
              <label className="block text-[12.5px] font-semibold text-ink-2" htmlFor="email">
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
                {!busy && <ArrowRight className="h-[15px] w-[15px]" />}
              </button>
            </form>
          ) : (
            <form onSubmit={onVerify} className="space-y-3.5">
              <p className="text-sm text-ink-2">
                We sent a 6-digit code to <span className="font-semibold text-ink">{email}</span>.
              </p>
              <input
                id="code"
                inputMode="numeric"
                autoFocus
                required
                value={code}
                onChange={(ev) => setCode(ev.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="123456"
                className={`${input} text-center font-serif text-2xl tracking-[0.4em] tnum`}
              />
              <button type="submit" disabled={busy || code.length < 6} className={primary}>
                {busy ? "Verifying…" : "Verify & sign in"}
              </button>
              <button
                type="button"
                onClick={() => setStep("email")}
                className="w-full text-center text-xs text-muted hover:text-ink"
              >
                Use a different email
              </button>
            </form>
          )}

          {error && (
            <p className="mt-4 rounded-xl bg-danger-soft px-3 py-2.5 text-xs text-danger">{error}</p>
          )}
        </div>
        <p className="mt-4 text-center text-[11px] text-muted">
          Operators only. Access is granted with <span className="font-mono">make grant-admin</span>.
        </p>
      </div>
    </div>
  );
}
