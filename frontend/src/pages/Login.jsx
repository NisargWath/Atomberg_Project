import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const demoRoles = [
  {
    label: "Continue as Employee",
    role: "Employee",
    email: "employee@demo.com",
    description: "Create goals and track quarterly progress.",
  },
  {
    label: "Continue as Manager",
    role: "Manager",
    email: "manager@demo.com",
    description: "Review approvals and monitor team performance.",
  },
  {
    label: "Continue as Admin",
    role: "Admin / HR",
    email: "admin@demo.com",
    description: "Monitor organization analytics, reports, and audit logs.",
  },
];

export default function Login() {
  const { login, loading } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [activeRole, setActiveRole] = useState("");

  async function submit(event) {
    event.preventDefault();
    await signIn(email, password, "manual");
  }

  async function demoSignIn(role) {
    await signIn(role.email, "password123", role.role);
  }

  async function signIn(selectedEmail, selectedPassword, roleLabel) {
    setError("");
    setActiveRole(roleLabel);
    try {
      await login(selectedEmail, selectedPassword);
      navigate("/dashboard");
    } catch (err) {
      setError(err.message);
    } finally {
      setActiveRole("");
    }
  }

  return (
    <main className="min-h-screen bg-[linear-gradient(135deg,#eef4ff_0%,#f8fafc_45%,#eefdf6_100%)] px-4 py-8 text-slate-950">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-6xl items-center">
        <div className="grid w-full gap-6 lg:grid-cols-[1.05fr_0.95fr]">
          <section className="rounded-2xl border border-white/70 bg-white/80 p-8 shadow-xl shadow-slate-200/70 backdrop-blur md:p-10">
            <Link className="text-sm font-semibold text-blue-700" to="/">Back to overview</Link>
            <p className="mt-8 text-sm font-semibold uppercase tracking-wide text-blue-700">ATOMQUEST PERFORMANCE PORTAL</p>
            <h1 className="mt-3 text-4xl font-bold leading-tight md:text-5xl">Enterprise Goal Setting & Tracking System</h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-slate-600">
              Manage employee goals, approvals, quarterly reviews, and organizational performance from a centralized platform.
            </p>

            <div className="mt-8 grid gap-3">
              {demoRoles.map((item) => (
                <button
                  className="group flex w-full items-center justify-between rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-blue-300 hover:bg-blue-50/40 hover:shadow-md disabled:opacity-60"
                  disabled={loading}
                  key={item.role}
                  onClick={() => demoSignIn(item)}
                >
                  <span>
                    <span className="block font-semibold text-slate-950">{item.label}</span>
                    <span className="mt-1 block text-sm text-slate-500">{item.description}</span>
                  </span>
                  <span className="ml-4 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-100 text-blue-700 transition group-hover:bg-blue-700 group-hover:text-white">
                    {loading && activeRole === item.role ? "..." : "→"}
                  </span>
                </button>
              ))}
            </div>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xl shadow-slate-200/70 md:p-8">
            <div className="rounded-xl bg-slate-950 p-5 text-white">
              <p className="text-sm font-semibold text-blue-200">Secure access</p>
              <h2 className="mt-2 text-2xl font-semibold">Sign in manually</h2>
              <p className="mt-2 text-sm leading-6 text-slate-300">Use this form for custom users created by Admin / HR.</p>
            </div>

            <form className="mt-6 space-y-4" onSubmit={submit}>
              <label className="block text-sm font-medium">
                Email
                <input className="field mt-1" placeholder="name@company.com" value={email} onChange={(event) => setEmail(event.target.value)} />
              </label>
              <label className="block text-sm font-medium">
                Password
                <input className="field mt-1" placeholder="Enter password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
              </label>
              {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}
              <button className="btn-primary w-full py-3" disabled={loading || !email || !password}>
                {loading && activeRole === "manual" ? "Signing in..." : "Sign in"}
              </button>
            </form>

            <div className="mt-8 grid gap-3">
              {demoRoles.map((item) => (
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-4" key={item.role}>
                  <p className="font-semibold">{item.role}</p>
                  <p className="mt-1 text-sm text-slate-500">{item.description}</p>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
