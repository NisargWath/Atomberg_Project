const styles = {
  approved: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  submitted: "bg-amber-50 text-amber-700 ring-amber-200",
  draft: "bg-slate-100 text-slate-700 ring-slate-200",
  rework: "bg-orange-50 text-orange-700 ring-orange-200",
  rejected: "bg-red-50 text-red-700 ring-red-200",
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  on_track: "bg-blue-50 text-blue-700 ring-blue-200",
  not_started: "bg-slate-100 text-slate-700 ring-slate-200"
};

export default function StatusBadge({ value }) {
  const key = value || "draft";
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold capitalize ring-1 ${styles[key] || styles.draft}`}>
      {String(key).replace("_", " ")}
    </span>
  );
}
