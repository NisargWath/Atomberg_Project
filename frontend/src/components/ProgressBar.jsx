export default function ProgressBar({ value = 0 }) {
  const safe = Math.max(0, Math.min(Number(value) || 0, 100));
  return (
    <div className="h-2 w-full rounded-full bg-slate-100">
      <div className="h-2 rounded-full bg-blue-700" style={{ width: `${safe}%` }} />
    </div>
  );
}
