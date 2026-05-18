export default function ChartCard({ title, subtitle, children }) {
  return (
    <div className="card min-h-80">
      <div className="mb-4">
        <h3 className="font-semibold text-slate-950">{title}</h3>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}
