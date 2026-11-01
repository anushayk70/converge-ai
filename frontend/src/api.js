// Thin fetch wrapper. Every error is turned into a readable message (the backend sends {detail}).
async function request(path, opts = {}) {
  let res;
  try {
    res = await fetch("/api" + path, opts);
  } catch {
    throw new Error("Cannot reach the Converge AI server. Is `python run.py` running?");
  }
  if (!res.ok) {
    let msg = `Request failed (HTTP ${res.status})`;
    try {
      const j = await res.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : j.detail.map((d) => d.msg).join("; ");
    } catch {}
    throw new Error(msg);
  }
  return res.json();
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  capabilities: () => request("/capabilities"),
  workspaces: () => request("/workspaces"),
  workspace: (id) => request(`/workspaces/${id}`),
  createWorkspace: (name, description) => request("/workspaces", json("POST", { name, description })),
  deleteWorkspace: (id) => request(`/workspaces/${id}`, { method: "DELETE" }),
  demo: () => request("/demo", { method: "POST" }),
  upload: (id, files) => {
    const fd = new FormData();
    [...files].forEach((f) => fd.append("files", f));
    return request(`/workspaces/${id}/resources/upload`, { method: "POST", body: fd });
  },
  addUrl: (id, url) => request(`/workspaces/${id}/resources/url`, json("POST", { url })),
  addText: (id, name, text) => request(`/workspaces/${id}/resources/text`, json("POST", { name, text })),
  removeResource: (rid) => request(`/resources/${rid}`, { method: "DELETE" }),
  retryResource: (rid) => request(`/resources/${rid}/retry`, { method: "POST" }),
  units: (rid) => request(`/resources/${rid}/units`),
  process: (id) => request(`/workspaces/${id}/process`, { method: "POST" }),
  job: (id) => request(`/workspaces/${id}/job`),
  report: (id) => request(`/workspaces/${id}/report`),
  chat: (id, question, history) => request(`/workspaces/${id}/chat`, json("POST", { question, history })),
  exportUrl: (id, fmt) => `/api/workspaces/${id}/export/${fmt}`,
};

export const KIND_LABEL = { pdf: "PDF", docx: "DOCX", txt: "TXT", html: "HTML", url: "Web page", text: "Pasted text", image: "Image", audio: "Audio", video: "Video" };
export const timeAgo = (ts) => {
  if (!ts) return "";
  const s = Math.max(1, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
