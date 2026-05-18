import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const itemClass = ({ isActive }) =>
  `block rounded-md px-3 py-2 text-sm font-medium ${isActive ? "bg-blue-700 text-white" : "text-slate-700 hover:bg-slate-100"}`;

export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-screen md:flex">
      <aside className="border-r border-slate-200 bg-white p-4 md:w-64">
        <h1 className="text-lg font-semibold text-slate-950">Goal Portal</h1>
        <p className="mt-1 text-xs text-slate-500">AtomQuest Hackathon 2026</p>
        <nav className="mt-6 space-y-1">
          <NavLink to="/dashboard" className={itemClass}>Dashboard</NavLink>
          <NavLink to="/goals" className={itemClass}>My Goals</NavLink>
          {["manager", "admin"].includes(user.role) && <NavLink to="/manager" className={itemClass}>Manager Review</NavLink>}
          {user.role === "admin" && <NavLink to="/admin" className={itemClass}>Admin / HR</NavLink>}
          {user.role === "admin" && <NavLink to="/admin/audit" className={itemClass}>Audit Logs</NavLink>}
        </nav>
        <div className="mt-8 rounded-md bg-slate-50 p-3 text-sm">
          <p className="font-medium">{user.name}</p>
          <p className="capitalize text-slate-500">{user.role}</p>
          <button className="mt-3 text-sm font-medium text-blue-700" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main className="flex-1 p-4 md:p-6">
        <Outlet />
      </main>
    </div>
  );
}
