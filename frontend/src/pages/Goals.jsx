import { useEffect, useMemo, useState } from "react";
import { EmptyState, LoadingState } from "../components/Feedback";
import StatusBadge from "../components/StatusBadge";
import { api } from "../lib/api";

const emptyGoal = {
  title: "",
  description: "",
  thrust_area: "Customer Experience",
  uom: "numeric",
  metric_type: "min",
  target: "",
  deadline: "",
  weightage: 10
};

export default function Goals() {
  const [goals, setGoals] = useState([]);
  const [updates, setUpdates] = useState([]);
  const [form, setForm] = useState(emptyGoal);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const editableGoals = useMemo(() => goals.filter((goal) => ["draft", "rework"].includes(goal.approval_status)), [goals]);
  const activeGoals = useMemo(() => goals.filter((goal) => goal.approval_status !== "rejected"), [goals]);
  const totalWeight = useMemo(() => activeGoals.reduce((sum, goal) => sum + Number(goal.weightage || 0), 0), [activeGoals]);
  const formErrors = {
    title: !form.title.trim() ? "Goal title is required" : "",
    description: !form.description.trim() ? "Description is required" : "",
    target: form.target === "" ? "Target is required" : "",
    weightage: Number(form.weightage) < 10 ? "Minimum weightage is 10%" : "",
    maxGoals: goals.length >= 8 ? "Maximum 8 goals per employee reached" : ""
  };
  const canCreate = !Object.values(formErrors).some(Boolean);
  const canSubmit = editableGoals.length > 0 && totalWeight === 100 && activeGoals.every((goal) => Number(goal.weightage) >= 10);

  function load() {
    setLoading(true);
    Promise.all([api("/api/goals"), api("/api/goals/quarterly-updates")])
      .then(([goalData, updateData]) => {
        setGoals(goalData.goals);
        setUpdates(updateData.updates);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function createGoal(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    if (!canCreate) return;
    setSaving(true);
    try {
      await api("/api/goals", { method: "POST", body: JSON.stringify(form) });
      setForm(emptyGoal);
      setMessage("Goal saved as draft.");
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function submitGoals() {
    setError("");
    setMessage("");
    try {
      await api("/api/goals/submit", { method: "POST", body: JSON.stringify({}) });
      setMessage("Goals submitted to manager.");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function updateWeight(goal, weightage) {
    await api(`/api/goals/${goal.id}`, { method: "PUT", body: JSON.stringify({ weightage }) });
    load();
  }

  async function addQuarterly(goal) {
    const actual = window.prompt("Actual achievement");
    if (actual === null) return;
    await api(`/api/goals/${goal.id}/quarterly-updates`, {
      method: "POST",
      body: JSON.stringify({ quarter: "Q1", actual_achievement: Number(actual), status: "on_track" })
    });
    load();
  }

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-2xl font-semibold text-slate-950">My Goals</h2>
        <p className="text-sm text-slate-500">Create up to 8 goals. Total weightage must equal 100% before submission.</p>
      </div>
      {message && <p className="rounded-md bg-green-50 p-3 text-sm text-green-700">{message}</p>}
      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      <form className="card grid gap-3 md:grid-cols-3" onSubmit={createGoal}>
        <input className="field" placeholder="Goal title" value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} />
        <input className="field" placeholder="Thrust area" value={form.thrust_area} onChange={(event) => setForm({ ...form, thrust_area: event.target.value })} />
        <select className="field" value={form.uom} onChange={(event) => setForm({ ...form, uom: event.target.value, metric_type: event.target.value === "timeline" ? "timeline" : event.target.value === "zero_based" ? "zero_based" : form.metric_type })}>
          <option value="numeric">Numeric</option>
          <option value="percentage">Percentage</option>
          <option value="timeline">Timeline</option>
          <option value="zero_based">Zero-based</option>
        </select>
        <select className="field" value={form.metric_type} onChange={(event) => setForm({ ...form, metric_type: event.target.value })}>
          <option value="min">Min type: Achievement / Target</option>
          <option value="max">Max type: Target / Achievement</option>
          <option value="timeline">Timeline</option>
          <option value="zero_based">Zero-based</option>
        </select>
        <input className="field" placeholder="Target" value={form.target} onChange={(event) => setForm({ ...form, target: event.target.value })} />
        <input className="field" type="date" value={form.deadline} onChange={(event) => setForm({ ...form, deadline: event.target.value })} />
        <input className="field md:col-span-2" placeholder="Description" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
        <input className="field" type="number" min="10" placeholder="Weightage" value={form.weightage} onChange={(event) => setForm({ ...form, weightage: event.target.value })} />
        <div className="md:col-span-3 grid gap-1 text-xs text-red-600">
          {Object.values(formErrors).filter(Boolean).map((item) => <span key={item}>{item}</span>)}
        </div>
        <button className="btn-primary md:col-span-3" disabled={!canCreate || saving}>{saving ? "Saving..." : "Save draft"}</button>
      </form>

      <div className="card">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h3 className="font-semibold">Goal sheet</h3>
            <p className={`text-sm ${totalWeight === 100 ? "text-green-700" : "text-slate-500"}`}>Active goal sheet weightage: {totalWeight}%</p>
            <p className="text-xs text-slate-500">
              Submission is enabled when approved, submitted, draft, and rework goals total exactly 100%, and at least one draft/rework goal is ready.
            </p>
            {!canSubmit && editableGoals.length > 0 && (
              <p className="mt-1 text-xs text-amber-700">
                Need {totalWeight < 100 ? `${100 - totalWeight}% more` : `${totalWeight - 100}% less`} weightage before submitting.
              </p>
            )}
          </div>
          <button className="btn-primary" disabled={!canSubmit} onClick={submitGoals}>Submit goals</button>
        </div>
        {loading && <div className="mt-4"><LoadingState /></div>}
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b text-slate-500">
              <tr><th className="py-2">Goal</th><th>Target</th><th>Weight</th><th>Status</th><th>Shared</th><th>Action</th></tr>
            </thead>
            <tbody>
              {goals.map((goal) => (
                <tr className="border-b last:border-0" key={goal.id}>
                  <td className="py-2"><p className="font-medium">{goal.title}</p><p className="text-slate-500">{goal.thrust_area}</p></td>
                  <td>{goal.target}</td>
                  <td><input className="field w-24" type="number" min="10" value={goal.weightage} disabled={goal.locked} onChange={(event) => updateWeight(goal, event.target.value)} /></td>
                  <td><StatusBadge value={goal.approval_status} /></td>
                  <td>{goal.is_shared ? "Yes" : "No"}</td>
                  <td><button className="btn-secondary" disabled={!goal.locked} onClick={() => addQuarterly(goal)}>Update Q1</button></td>
                </tr>
              ))}
              {!goals.length && !loading && <tr><td className="py-4" colSpan="6"><EmptyState title="No goals yet" detail="Create draft goals, reach 100% total weightage, then submit to your manager." /></td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h3 className="font-semibold">Quarterly progress</h3>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {updates.map((item) => (
            <div className="rounded-md border border-slate-200 p-3" key={item.id}>
              <p className="font-medium">{item.quarter}</p>
              <p className="text-sm text-slate-500">Actual: {item.actual_achievement}</p>
              <p className="text-sm text-slate-500">Progress: {item.progress}%</p>
            </div>
          ))}
          {!updates.length && <p className="text-sm text-slate-500">No progress updates yet.</p>}
        </div>
      </div>
    </section>
  );
}
