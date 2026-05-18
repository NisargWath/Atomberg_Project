import { Link } from "react-router-dom";
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer } from "recharts";

const features = ["Goal Tracking", "Quarterly Reviews", "Manager Approvals", "Shared KPIs", "Audit Logs", "Analytics Dashboard"];
const preview = [
  { name: "Approved", value: 68, color: "#2563eb" },
  { name: "Pending", value: 18, color: "#f59e0b" },
  { name: "Draft", value: 14, color: "#64748b" }
];
const progress = [
  { name: "Q1", value: 61 },
  { name: "Q2", value: 74 },
  { name: "Q3", value: 82 },
  { name: "Q4", value: 90 }
];

export default function Landing() {
  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <section className="border-b border-slate-200 bg-white">
        <div className="mx-auto grid max-w-7xl gap-10 px-6 py-16 lg:grid-cols-[1.1fr_0.9fr] lg:py-20">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wide text-blue-700">AtomQuest Hackathon 2026</p>
            <h1 className="mt-4 max-w-3xl text-4xl font-bold leading-tight md:text-6xl">Goal Setting & Tracking Portal</h1>
            <p className="mt-5 max-w-2xl text-lg leading-8 text-slate-600">
              A centralized performance workflow for employees, managers, and HR teams to plan goals, approve targets, track quarterly achievements, and monitor progress with audit-ready reporting.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link className="btn-primary px-5" to="/login">Login to portal</Link>
              <Link className="btn-secondary px-5" to="/login">Choose demo role</Link>
            </div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-5 shadow-sm">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-lg border border-slate-200 bg-white p-4">
                <p className="text-sm font-medium text-slate-500">Goal status</p>
                <ResponsiveContainer width="100%" height={180}>
                  <PieChart>
                    <Pie data={preview} dataKey="value" innerRadius={42} outerRadius={70} paddingAngle={3}>
                      {preview.map((item) => <Cell key={item.name} fill={item.color} />)}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="rounded-lg border border-slate-200 bg-white p-4">
                <p className="text-sm font-medium text-slate-500">Quarterly progress</p>
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={progress}>
                    <Bar dataKey="value" fill="#2563eb" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              {["Employee dashboard", "Manager dashboard", "Admin dashboard"].map((item) => (
                <div className="rounded-lg border border-slate-200 bg-white p-4" key={item}>
                  <p className="text-sm font-semibold">{item}</p>
                  <p className="mt-2 text-xs text-slate-500">Real-time workflow metrics from backend APIs.</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-6 py-12">
        <div className="grid gap-4 md:grid-cols-3">
          {features.map((feature) => (
            <div className="card" key={feature}>
              <h3 className="font-semibold">{feature}</h3>
              <p className="mt-2 text-sm text-slate-500">Production-minded workflow support for the final hackathon demo.</p>
            </div>
          ))}
        </div>
      </section>

      <section className="border-y border-slate-200 bg-white">
        <div className="mx-auto grid max-w-7xl gap-8 px-6 py-12 lg:grid-cols-3">
          {["Employee creates goals", "Manager approves and reviews", "Admin monitors and exports"].map((step, index) => (
            <div className="rounded-lg border border-slate-200 p-5" key={step}>
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-blue-700 text-sm font-semibold text-white">{index + 1}</span>
              <h3 className="mt-4 font-semibold">{step}</h3>
              <p className="mt-2 text-sm text-slate-500">Clear handoffs replace spreadsheet and email-based goal tracking.</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-6 py-12">
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="card">
            <h2 className="text-xl font-semibold">Demo Access</h2>
            <p className="mt-3 text-sm leading-6 text-slate-500">
              Judges can enter the portal with one-click role-based access from the login page. No setup or credential sharing is required during the demo.
            </p>
            <Link className="btn-primary mt-5" to="/login">Open demo login</Link>
          </div>
          <div className="card">
            <h2 className="text-xl font-semibold">Tech Stack</h2>
            <div className="mt-4 flex flex-wrap gap-2">
              {["React", "Flask", "SQLite", "JWT Authentication", "Recharts", "Tailwind CSS"].map((item) => (
                <span className="rounded-full bg-slate-100 px-3 py-1 text-sm font-medium text-slate-700" key={item}>{item}</span>
              ))}
            </div>
          </div>
        </div>
      </section>

      <footer className="border-t border-slate-200 bg-white px-6 py-6 text-sm text-slate-500">
        <div className="mx-auto flex max-w-7xl flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <p>AtomQuest Hackathon 2026</p>
          <p>Participant: Nisarg Wath · GitHub: add-your-repo-link</p>
        </div>
      </footer>
    </main>
  );
}
