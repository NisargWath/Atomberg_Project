import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    const raw = localStorage.getItem("goal_portal_user");
    return raw ? JSON.parse(raw) : null;
  });
  const [loading, setLoading] = useState(false);

  async function login(email, password) {
    setLoading(true);
    try {
      const data = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password })
      });
      localStorage.setItem("goal_portal_token", data.token);
      localStorage.setItem("goal_portal_user", JSON.stringify(data.user));
      setUser(data.user);
      return data.user;
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem("goal_portal_token");
    localStorage.removeItem("goal_portal_user");
    setUser(null);
  }

  useEffect(() => {
    if (!localStorage.getItem("goal_portal_token")) return;
    api("/api/auth/me")
      .then((data) => {
        localStorage.setItem("goal_portal_user", JSON.stringify(data.user));
        setUser(data.user);
      })
      .catch(logout);
  }, []);

  const value = useMemo(() => ({ user, login, logout, loading }), [user, loading]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
