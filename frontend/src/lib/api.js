export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:5001";

export async function api(path, options = {}) {
  const token = localStorage.getItem("goal_portal_token");
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers || {})
      }
    });
  } catch {
    throw new Error(`Cannot reach backend at ${API_URL}. Start Flask or set VITE_API_URL to the backend port.`);
  }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(data?.message || "Request failed");
  }
  return data.data ?? data;
}

export function reportUrl(type = "goals") {
  return `${API_URL}/api/admin/reports/export?type=${type}`;
}

export async function downloadReport(type = "goals") {
  const token = localStorage.getItem("goal_portal_token");
  const response = await fetch(reportUrl(type), {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    }
  });
  if (!response.ok) {
    throw new Error("Report export failed. Please sign in as Admin/HR again.");
  }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${type}-report.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
