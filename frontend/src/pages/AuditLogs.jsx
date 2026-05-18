import { useEffect, useMemo, useState } from "react";
import { EmptyState, LoadingState } from "../components/Feedback";
import StatusBadge from "../components/StatusBadge";
import { api } from "../lib/api";

function formatValue(value) {
  if (!value) return "-";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export default function AuditLogs() {
  const [logs, setLogs] = useState([]);
  const [search, setSearch] = useState("");
  const [action, setAction] = useState("");
  const [sort, setSort] = useState("newest");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/admin/audit-logs")
      .then((data) => setLogs(data.logs || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const actions = useMemo(() => [...new Set(logs.map((log) => log.action))].sort(), [logs]);
  const filtered = useMemo(() => {
    const text = search.toLowerCase();
    return logs
      .filter((log) => !action || log.action === action)
      .filter((log) => !text || [log.actor_name, log.action, log.entity_type, log.entity_id].join(" ").toLowerCase().includes(text))
      .sort((a, b) => sort === "newest" ? new Date(b.created_at) - new Date(a.created_at) : new Date(a.created_at) - new Date(b.created_at));
  }, [logs, search, action, sort]);

  if (loading) return <LoadingState label="Loading audit trail..." />;

  return (
    <section className="space-y-5">
      <div>
        <p className="text-sm font-semibold uppercase tracking-wide text-blue-700">Enterprise audit trail</p>
        <h2 className="text-2xl font-semibold text-slate-950">Audit Logs</h2>
        <p className="text-sm text-slate-500">Searchable history of user actions, entity changes, and report exports.</p>
      </div>
      {error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      <div className="card grid gap-3 md:grid-cols-3">
        <input className="field" placeholder="Search user, action, entity..." value={search} onChange={(event) => setSearch(event.target.value)} />
        <select className="field" value={action} onChange={(event) => setAction(event.target.value)}>
          <option value="">All actions</option>
          {actions.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <select className="field" value={sort} onChange={(event) => setSort(event.target.value)}>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
        </select>
      </div>

      <div className="card overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1000px] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-3">User</th>
                <th className="px-4 py-3">Action</th>
                <th className="px-4 py-3">Entity</th>
                <th className="px-4 py-3">Previous Value</th>
                <th className="px-4 py-3">Updated Value</th>
                <th className="px-4 py-3">Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((log) => (
                <tr className="border-t border-slate-100 align-top" key={log.id}>
                  <td className="px-4 py-3">
                    <p className="font-medium">{log.actor_name}</p>
                    <p className="text-xs capitalize text-slate-500">{log.actor_role}</p>
                  </td>
                  <td className="px-4 py-3"><StatusBadge value={log.action?.toLowerCase()} /></td>
                  <td className="px-4 py-3">
                    <p>{log.entity_type}</p>
                    <p className="text-xs text-slate-500">{log.entity_id}</p>
                  </td>
                  <td className="max-w-xs px-4 py-3 text-xs text-slate-600">{formatValue(log.previous_value)}</td>
                  <td className="max-w-xs px-4 py-3 text-xs text-slate-600">{formatValue(log.updated_value)}</td>
                  <td className="px-4 py-3 text-slate-500">{new Date(log.created_at).toLocaleString()}</td>
                </tr>
              ))}
              {!filtered.length && (
                <tr><td className="px-4 py-8" colSpan="6"><EmptyState title="No audit logs match" detail="Adjust search or filters to inspect a wider audit trail." /></td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
