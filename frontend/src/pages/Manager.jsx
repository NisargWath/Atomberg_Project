import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import ChartCard from "../components/ChartCard";
import { LoadingState } from "../components/Feedback";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import { api } from "../lib/api";

export default function Manager() {
  const [submissions, setSubmissions] = useState([]);
  const [users, setUsers] = useState([]);
  const [updates, setUpdates] = useState([]);
  const [dashboard, setDashboard] = useState({ stats: {}, chart_data: {} });
  const [shared, setShared] = useState({ title: "", description: "", thrust_area: "Department KPI", uom: "numeric", metric_type: "min", target: "", weightage: 10, employee_ids: [] });
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  function load() {
    setLoading(true);
    Promise.all([api("/api/manager/submissions"), api("/api/admin/users"), api("/api/goals/quarterly-updates"), api("/api/manager/dashboard")])
      .then(([submissionData, userData, updateData, dashboardData]) => {
        setSubmissions(submissionData.submissions);
        setUsers(userData.users);
        setUpdates(updateData.updates);
        setDashboard(dashboardData);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function decide(goal, decision) {
    setError("");
    setMessage("");
    const comment = decision === "approved" ? "Approved" : window.prompt("Comment for employee") || "";
    try {
      await api(`/api/manager/goals/${goal.id}/decision`, { method: "POST", body: JSON.stringify({ decision, comment }) });
      setMessage(`Goal ${decision}.`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function assignShared(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      await api("/api/manager/shared-goals", { method: "POST", body: JSON.stringify(shared) });
      setShared({ ...shared, title: "", description: "", target: "", employee_ids: [] });
      setMessage("Shared departmental KPI assigned.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function reviewUpdate(update) {
    const comment = window.prompt("Manager check-in feedback");
    if (!comment) return;
    await api(`/api/manager/updates/${update.id}/review`, { method: "POST", body: JSON.stringify({ comment }) });
    load();
  }

  const employees = users.filter((user) => user.role === "employee");

  if (loading) return <LoadingState label="Loading manager workspace..." />;

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-2xl font-semibold text-slate-950">Manager Review</h2>
        <p className="text-sm text-slate-500">Approve submitted goals, return rework, and assign shared KPIs.</p>
      </div>
      {message && <p className="rounded-md bg-green-50 p-3 text-sm text-green-700">{message}</p>}
      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Team Members" value={dashboard.stats.team_members} />
        <StatCard label="Pending Approvals" value={dashboard.stats.pending_approvals} />
        <StatCard label="Approved Goals" value={dashboard.stats.approved_goals} />
        <StatCard label="Team Progress" value={`${dashboard.stats.average_progress || 0}%`} />
      </div>

      <ChartCard title="Team Completion Overview" subtitle="Average progress by employee from quarterly updates">
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={dashboard.chart_data.employee_progress || []}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="name" />
            <YAxis domain={[0, 100]} />
            <Tooltip />
            <Bar dataKey="progress" fill="#2563eb" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      <div className="card">
        <h3 className="font-semibold">Pending approvals</h3>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b text-slate-500">
              <tr><th className="py-2">Goal</th><th>Target</th><th>Weight</th><th>Decision</th></tr>
            </thead>
            <tbody>
              {submissions.map((goal) => (
                <tr className="border-b last:border-0" key={goal.id}>
                  <td className="py-2"><p className="font-medium">{goal.title}</p><p className="text-slate-500">{goal.employee_id}</p></td>
                  <td>{goal.target}</td>
                  <td>{goal.weightage}%</td>
                  <td className="space-x-2">
                    <button className="btn-primary" onClick={() => decide(goal, "approved")}>Approve</button>
                    <button className="btn-secondary" onClick={() => decide(goal, "rework")}>Rework</button>
                    <button className="btn-danger" onClick={() => decide(goal, "rejected")}>Reject</button>
                  </td>
                </tr>
              ))}
              {!submissions.length && <tr><td className="py-4 text-slate-500" colSpan="4">No pending submissions.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <form className="card grid gap-3 md:grid-cols-3" onSubmit={assignShared}>
        <div className="md:col-span-3">
          <h3 className="font-semibold">Assign shared departmental KPI</h3>
        </div>
        <input className="field" placeholder="KPI title" value={shared.title} onChange={(event) => setShared({ ...shared, title: event.target.value })} />
        <input className="field" placeholder="Target" value={shared.target} onChange={(event) => setShared({ ...shared, target: event.target.value })} />
        <input className="field" type="number" min="10" value={shared.weightage} onChange={(event) => setShared({ ...shared, weightage: event.target.value })} />
        <input className="field md:col-span-2" placeholder="Description" value={shared.description} onChange={(event) => setShared({ ...shared, description: event.target.value })} />
        <select className="field" value={shared.uom} onChange={(event) => setShared({ ...shared, uom: event.target.value })}>
          <option value="numeric">Numeric</option>
          <option value="percentage">Percentage</option>
          <option value="timeline">Timeline</option>
          <option value="zero_based">Zero-based</option>
        </select>
        <div className="md:col-span-3 grid gap-2 md:grid-cols-3">
          {employees.map((employee) => (
            <label className="flex items-center gap-2 rounded-md border border-slate-200 p-2 text-sm" key={employee.id}>
              <input
                type="checkbox"
                checked={shared.employee_ids.includes(employee.id)}
                onChange={(event) => {
                  const employee_ids = event.target.checked
                    ? [...shared.employee_ids, employee.id]
                    : shared.employee_ids.filter((id) => id !== employee.id);
                  setShared({ ...shared, employee_ids });
                }}
              />
              {employee.name}
            </label>
          ))}
        </div>
        <button className="btn-primary md:col-span-3">Assign shared KPI</button>
      </form>

      <div className="card">
        <h3 className="font-semibold">Quarterly check-ins</h3>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b text-slate-500">
              <tr><th className="py-2">Employee</th><th>Quarter</th><th>Status</th><th>Progress</th><th>Feedback</th></tr>
            </thead>
            <tbody>
              {updates.map((update) => (
                <tr className="border-b last:border-0" key={update.id}>
                  <td className="py-2">{update.employee_id}</td>
                  <td>{update.quarter}</td>
                  <td><StatusBadge value={update.status} /></td>
                  <td>{update.progress}%</td>
                  <td><button className="btn-secondary" onClick={() => reviewUpdate(update)}>{update.manager_reviewed ? "Reviewed" : "Add comment"}</button></td>
                </tr>
              ))}
              {!updates.length && <tr><td className="py-4 text-slate-500" colSpan="5">No employee updates yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
