export function LoadingState({ label = "Loading data..." }) {
  return <div className="card text-sm text-slate-500">{label}</div>;
}

export function EmptyState({ title = "No data yet", detail = "Once activity is available, it will appear here." }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-center">
      <p className="font-medium text-slate-800">{title}</p>
      <p className="mt-1 text-sm text-slate-500">{detail}</p>
    </div>
  );
}
