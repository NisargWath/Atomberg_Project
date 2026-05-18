import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import AIInsightsCard from "../components/AIInsightsCard";
import ChartCard from "../components/ChartCard";
import { EmptyState, LoadingState } from "../components/Feedback";
import ProgressBar from "../components/ProgressBar";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import { useAuth } from "../context/AuthContext";
import { api } from "../lib/api";

const colors = ["#2563eb", "#059669", "#f59e0b", "#dc2626", "#64748b", "#7c3aed"];

export default function Dashboard() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const path = user.role === "admin" ? "/api/admin/dashboard" : user.role === "manager" ? "/api/manager/dashboard" : "/api/goals/dashboard";
    setLoading(true);
    api(path)
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [user.role]);

  if (loading) return <LoadingState label="Loading dashboard metrics..." />;
  if (error) return <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>;

  const stats = data?.stats || {};
  const chartData = data?.chart_data || {};
  const title = user.role === "admin" ? "Organization Dashboard" : user.role === "manager" ? "Manager Dashboard" : "Employee Dashboard";

  return (
    <section className="space-y-6">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-700">AtomQuest performance portal</p>
          <h2 className="text-2xl font-semibold text-slate-950">{title}</h2>
          <p className="text-sm text-slate-500">Live data from SQLite-backed APIs across goals, approvals, quarterly updates, and audit activity.</p>
        </div>
        <div className="rounded-full bg-white px-4 py-2 text-sm font-medium text-slate-600 ring-1 ring-slate-200">
          Signed in as <span className="capitalize text-slate-950">{user.role}</span>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Total Goals" value={stats.total_goals} hint={`${stats.approved_goals || 0} approved`} />
        <StatCard label="Pending Reviews" value={stats.pending_approvals ?? stats.pending_reviews} hint="Needs manager/admin action" />
        <StatCard label="Average Progress" value={`${stats.average_progress || 0}%`} hint="Latest quarterly updates" />
        <StatCard label={user.role === "employee" ? "Quarterly Updates" : "Active Users"} value={user.role === "employee" ? stats.quarterly_updates : stats.active_users || stats.team_members} hint="Real activity count" />
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <ChartCard title="Goal Status Mix" subtitle="Approved, pending, draft, rework, and rejected goals">
          {chartData.goals_by_status?.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={chartData.goals_by_status} dataKey="value" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={3}>
                  {chartData.goals_by_status.map((entry, index) => <Cell key={entry.name} fill={colors[index % colors.length]} />)}
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : <EmptyState />}
        </ChartCard>

        <ChartCard title="Quarterly Progress" subtitle="Average progress by quarter">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={chartData.quarterly_progress || []}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="quarter" />
              <YAxis domain={[0, 100]} />
              <Tooltip />
              <Line type="monotone" dataKey="progress" stroke="#2563eb" strokeWidth={3} dot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title={user.role === "employee" ? "Goal Progress" : "Team Progress"} subtitle="Latest completion by employee">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData.employee_progress || []}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} />
              <YAxis domain={[0, 100]} />
              <Tooltip />
              <Bar dataKey="progress" fill="#059669" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="card">
          <h3 className="font-semibold text-slate-950">Goal Progress Overview</h3>
          <div className="mt-4 space-y-4">
            {(data.goal_rows || []).slice(0, 8).map((goal) => (
              <div className="rounded-lg border border-slate-200 p-3" key={goal.id}>
                <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                  <div>
                    <p className="font-medium">{goal.title}</p>
                    <p className="text-sm text-slate-500">{goal.employee_name} · {goal.thrust_area}</p>
                  </div>
                  <StatusBadge value={goal.approval_status} />
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <ProgressBar value={goal.latest_progress} />
                  <span className="w-12 text-right text-sm font-semibold">{goal.latest_progress || 0}%</span>
                </div>
              </div>
            ))}
            {!data.goal_rows?.length && <EmptyState title="No goals available" detail="Seed demo data or create goals to populate this dashboard." />}
          </div>
        </div>

        <div className="space-y-5">
          <AIInsightsCard role={user.role} />
          <div className="card">
            <h3 className="font-semibold text-slate-950">Recent Updates</h3>
            <div className="mt-4 space-y-3">
              {(data.recent_updates || []).slice(0, 6).map((item) => (
                <div className="rounded-lg bg-slate-50 p-3" key={item.id}>
                  <div className="flex items-center justify-between">
                    <p className="font-medium">{item.quarter}</p>
                    <StatusBadge value={item.status} />
                  </div>
                  <p className="mt-1 text-sm text-slate-500">Actual {item.actual_achievement} · Progress {item.progress}%</p>
                </div>
              ))}
              {!data.recent_updates?.length && <EmptyState title="No quarterly updates" detail="Approved goals can receive quarterly progress updates." />}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
