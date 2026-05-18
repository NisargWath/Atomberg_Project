import { useEffect, useState } from "react";
import { api } from "../lib/api";

const endpointByRole = {
  employee: "/api/ai/employee-insights",
  manager: "/api/ai/manager-insights",
  admin: "/api/ai/admin-insights"
};

const titleByRole = {
  employee: "AI Goal Insights",
  manager: "AI Team Insights",
  admin: "AI Organization Insights"
};

export default function AIInsightsCard({ role }) {
  const [insights, setInsights] = useState([]);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  async function loadInsights() {
    setLoading(true);
    setMessage("");
    try {
      const data = await api(endpointByRole[role] || endpointByRole.employee);
      setInsights(data.insights || []);
      setMessage(data.available === false ? data.message || "AI insights temporarily unavailable." : "");
    } catch {
      setInsights([]);
      setMessage("AI insights temporarily unavailable.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadInsights();
  }, [role]);

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 text-sm font-bold text-blue-700">AI</span>
            <h3 className="font-semibold text-slate-950">{titleByRole[role] || "AI Performance Insights"}</h3>
          </div>
          <p className="mt-1 text-sm text-slate-500">Optional performance summary generated from live dashboard data.</p>
        </div>
        <button className="btn-secondary shrink-0" onClick={loadInsights} disabled={loading}>
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      <div className="mt-4">
        {loading && <p className="text-sm text-slate-500">Generating concise performance insights...</p>}
        {!loading && message && <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-700">{message}</p>}
        {!loading && insights.length > 0 && (
          <ul className="space-y-3 text-sm text-slate-700">
            {insights.map((item) => (
              <li className="flex gap-2" key={item}>
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-700" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        )}
        {!loading && !message && insights.length === 0 && (
          <p className="text-sm text-slate-500">AI insights temporarily unavailable.</p>
        )}
      </div>
    </div>
  );
}
