import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState, LoadingState } from "../components/Feedback";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import { api, downloadReport } from "../lib/api";

const emptyUser = { name: "", email: "", role: "employee", department: "Product", manager_id: "", password: "password123" };
const reports = [
  ["Employee goals", "goals"],
  ["Quarterly achievements", "quarterly"],
  ["Team reports", "team"],
  ["Organization performance", "organization"]
];

export default function Admin() {
  const [dashboard, setDashboard] = useState({ stats: {}, goal_rows: [] });
  const [users, setUsers] = useState([]);
  const [userForm, setUserForm] = useState(emptyUser);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  function load() {
    setLoading(true);
    Promise.all([api("/api/admin/dashboard"), api("/api/admin/users")])
      .then(([dashboardData, userData]) => {
        setDashboard(dashboardData);
        setUsers(userData.users);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function createUser(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    if (!userForm.name || !userForm.email) {
      setError("Name and email are required.");
      return;
    }
    try {
      await api("/api/admin/users", { method: "POST", body: JSON.stringify(userForm) });
      setUserForm(emptyUser);
      setMessage("User created successfully.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function unlock(goal) {
    setMessage("");
    setError("");
    try {
      await api(`/api/admin/goals/${goal.id}/unlock`, { method: "POST", body: JSON.stringify({}) });
      setMessage("Goal unlocked and returned for rework.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function exportReport(type) {
    setMessage("");
    setError("");
    try {
      await downloadReport(type);
      setMessage("Report downloaded successfully.");
    } catch (err) {
      setError(err.message);
    }
  }

  if (loading) return <LoadingState label="Loading admin workspace..." />;

  return (
    <section className="space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-700">Admin / HR control center</p>
          <h2 className="text-2xl font-semibold text-slate-950">Organization Monitoring</h2>
          <p className="text-sm text-slate-500">Manage users, unlock goals, inspect reports, and keep the demo flow moving.</p>
        </div>
        <Link className="btn-secondary" to="/admin/audit">Open audit logs</Link>
      </div>
      {message && <p className="rounded-md bg-green-50 p-3 text-sm text-green-700">{message}</p>}
      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Employees" value={dashboard.stats.employees} />
        <StatCard label="Managers" value={dashboard.stats.managers} />
        <StatCard label="Pending Reviews" value={dashboard.stats.pending_approvals} />
        <StatCard label="Avg Progress" value={`${dashboard.stats.average_progress ?? 0}%`} />
      </div>

      <div className="card">
        <h3 className="font-semibold">Export Reports</h3>
        <p className="mt-1 text-sm text-slate-500">CSV exports are generated from live SQLite-backed API data.</p>
        <div className="mt-4 grid gap-3 md:grid-cols-4">
          {reports.map(([label, type]) => (
            <button className="btn-secondary" type="button" onClick={() => exportReport(type)} key={type}>{label}</button>
          ))}
        </div>
      </div>

      <div className="card overflow-hidden p-0">
        <div className="flex items-center justify-between border-b border-slate-100 p-4">
          <div>
            <h3 className="font-semibold">Organization Goals</h3>
            <p className="text-sm text-slate-500">Approved goals can be unlocked by Admin/HR for rework.</p>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr><th className="px-4 py-3">Goal</th><th>Employee</th><th>Manager</th><th>Weight</th><th>Status</th><th>Progress</th><th>Action</th></tr>
            </thead>
            <tbody>
              {(dashboard.goal_rows || []).map((goal) => (
                <tr className="border-t border-slate-100" key={goal.id}>
                  <td className="px-4 py-3"><p className="font-medium">{goal.title}</p><p className="text-xs text-slate-500">{goal.thrust_area}</p></td>
                  <td className="px-4 py-3">{goal.employee_name}</td>
                  <td className="px-4 py-3">{goal.manager_name}</td>
                  <td className="px-4 py-3">{goal.weightage}%</td>
                  <td className="px-4 py-3"><StatusBadge value={goal.approval_status} /></td>
                  <td className="px-4 py-3">{goal.latest_progress || 0}%</td>
                  <td className="px-4 py-3"><button className="btn-secondary" disabled={!goal.locked} onClick={() => unlock(goal)}>Unlock</button></td>
                </tr>
              ))}
              {!dashboard.goal_rows?.length && <tr><td className="px-4 py-8" colSpan="7"><EmptyState title="No goals found" detail="Run the demo seeder or create goals to populate organization reporting." /></td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <form className="card grid gap-3 md:grid-cols-3" onSubmit={createUser}>
        <div className="md:col-span-3">
          <h3 className="font-semibold">Manage Users</h3>
          <p className="mt-1 text-sm text-slate-500">Create employees, managers, or Admin/HR users for the demo flow.</p>
        </div>
        <input className="field" placeholder="Name" value={userForm.name} onChange={(event) => setUserForm({ ...userForm, name: event.target.value })} />
        <input className="field" placeholder="Email" value={userForm.email} onChange={(event) => setUserForm({ ...userForm, email: event.target.value })} />
        <select className="field" value={userForm.role} onChange={(event) => setUserForm({ ...userForm, role: event.target.value })}>
          <option value="employee">Employee</option>
          <option value="manager">Manager</option>
          <option value="admin">Admin / HR</option>
        </select>
        <input className="field" placeholder="Department" value={userForm.department} onChange={(event) => setUserForm({ ...userForm, department: event.target.value })} />
        <select className="field" value={userForm.manager_id} onChange={(event) => setUserForm({ ...userForm, manager_id: event.target.value })}>
          <option value="">No manager</option>
          {users.filter((user) => user.role === "manager").map((manager) => <option key={manager.id} value={manager.id}>{manager.name}</option>)}
        </select>
        <input className="field" value={userForm.password} onChange={(event) => setUserForm({ ...userForm, password: event.target.value })} />
        <button className="btn-primary md:col-span-3">Create user</button>
      </form>
    </section>
  );
}
