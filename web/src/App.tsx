import { useState } from "react";
import {
  API_BASE,
  requestOtp,
  simulateRequestOtp,
  simulateVerifyOtp,
  verifyOtp,
} from "./api";
import SupportConsole from "./SupportConsole";

type Step = "email" | "code" | "done";

function App() {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [simulate, setSimulate] = useState(true);
  const [simCode, setSimCode] = useState<string | null>(null);
  const [tokens, setTokens] = useState<{ access: string; refresh: string } | null>(null);

  async function onSendCode(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    setSimCode(null);
    const res = simulate ? simulateRequestOtp(email) : await requestOtp(email);
    setBusy(false);
    if (res.ok) {
      if (simulate && res.code) setSimCode(res.code);
      setCode("");
      setStep("code");
    } else {
      setError(res.detail);
    }
  }

  async function onVerify(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const res = simulate ? simulateVerifyOtp(email, code) : await verifyOtp(email, code);
    setBusy(false);
    if (res.ok) {
      setTokens({ access: res.accessToken, refresh: res.refreshToken });
      setStep("done");
    } else {
      setError(res.detail);
    }
  }

  function reset() {
    setStep("email");
    setEmail("");
    setCode("");
    setError(null);
    setSimCode(null);
    setTokens(null);
  }

  // A real (non-simulated) sign-in opens the support console — the first real product screen.
  if (step === "done" && tokens && !simulate) {
    return <SupportConsole token={tokens.access} email={email} onSignOut={reset} />;
  }

  return (
    <div className="min-h-screen bg-neutral-50 text-neutral-900 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-semibold tracking-tight">Growth Operator</h1>
          <p className="text-sm text-neutral-500">Owner sign-in</p>
        </div>

        <div className="rounded-2xl border border-neutral-200 bg-white p-6 shadow-sm">
          {step === "email" && (
            <form onSubmit={onSendCode} className="space-y-4">
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
                className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900"
              />
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-lg bg-neutral-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-50"
              >
                {busy ? "Sending…" : "Send code"}
              </button>
            </form>
          )}

          {step === "code" && (
            <form onSubmit={onVerify} className="space-y-4">
              <p className="text-sm text-neutral-600">
                We sent a 6-digit code to <span className="font-medium">{email}</span>.
              </p>
              {simCode && (
                <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  Simulated code (dev echo):{" "}
                  <span className="font-mono font-semibold">{simCode}</span>
                </div>
              )}
              <input
                id="code"
                inputMode="numeric"
                autoFocus
                required
                value={code}
                onChange={(ev) => setCode(ev.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="123456"
                className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-center text-lg font-mono tracking-[0.4em] outline-none focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900"
              />
              <button
                type="submit"
                disabled={busy || code.length < 6}
                className="w-full rounded-lg bg-neutral-900 px-3 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-50"
              >
                {busy ? "Verifying…" : "Verify & sign in"}
              </button>
              <button
                type="button"
                onClick={reset}
                className="w-full text-center text-xs text-neutral-500 hover:text-neutral-800"
              >
                Use a different email
              </button>
            </form>
          )}

          {step === "done" && tokens && (
            <div className="space-y-4 text-center">
              <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-green-100 text-green-700">
                ✓
              </div>
              <p className="text-sm font-medium">Signed in as {email}</p>
              <div className="rounded-lg bg-neutral-100 p-3 text-left">
                <p className="text-[11px] uppercase tracking-wide text-neutral-500">Access token</p>
                <p className="truncate font-mono text-xs">{tokens.access}</p>
              </div>
              <button
                type="button"
                onClick={reset}
                className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm font-medium hover:bg-neutral-50"
              >
                Sign out
              </button>
            </div>
          )}

          {error && (
            <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>
          )}
        </div>

        <div className="mt-4 flex items-center justify-between text-xs text-neutral-500">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={simulate}
              onChange={(ev) => setSimulate(ev.target.checked)}
            />
            Simulate (no backend)
          </label>
          <span className="font-mono">{simulate ? "demo" : API_BASE}</span>
        </div>
        <p className="mt-2 text-center text-[11px] text-neutral-400">
          MVP-011 · interim email OTP (phone paused while Meta WABA is deferred)
        </p>
      </div>
    </div>
  );
}

export default App;
