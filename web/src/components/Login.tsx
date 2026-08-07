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
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none " +
    "focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900";
  const primary =
    "w-full rounded-lg bg-neutral-900 px-3 py-2 text-sm font-medium text-white " +
    "transition hover:bg-neutral-700 disabled:opacity-50";

  return (
    <div className="flex min-h-screen items-center justify-center bg-neutral-50 p-4 text-neutral-900">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-semibold tracking-tight">Growth Operator</h1>
          <p className="text-sm text-neutral-500">Store sign-in</p>
        </div>

        <div className="rounded-2xl border border-neutral-200 bg-white p-6 shadow-sm">
          {step === "email" ? (
            <form onSubmit={onSend} className="space-y-4">
              <label className="block text-sm font-medium text-neutral-700" htmlFor="email">
                Email address
              </label>
              <input
                id="email"
                type="email"
                autoFocus
                required
                value={email}
                onChange={(ev) => setEmail(ev.target.value)}
                placeholder="you@store.com"
                className={input}
              />
              <button type="submit" disabled={busy} className={primary}>
                {busy ? "Sending…" : "Send code"}
              </button>
            </form>
          ) : (
            <form onSubmit={onVerify} className="space-y-4">
              <p className="text-sm text-neutral-600">
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
                className="w-full text-center text-xs text-neutral-500 hover:text-neutral-800"
              >
                Use a different email
              </button>
            </form>
          )}

          {error && (
            <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>
          )}
        </div>
        <p className="mt-3 text-center text-[11px] text-neutral-400">
          Local dev: set <span className="font-mono">GROWTH_OPERATOR_OTP_DEV_FIXED_CODE=000000</span>{" "}
          and sign in with 000000.
        </p>
      </div>
    </div>
  );
}
