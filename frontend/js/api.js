// HTTP helpers and job polling.  Every long request is a server-side job with
// staged progress; callers pass an AbortSignal so a newer request supersedes
// an older one (the server job is cancelled too).
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function handle(resp) {
  if (!resp.ok) {
    let detail = resp.statusText;
    try { const j = await resp.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) {}
    if (resp.status === 404 && detail === "Not Found") {
      detail = `The NADIR API endpoint ${new URL(resp.url).pathname} is unavailable. Restart the backend from this checkout (.venv/bin/python -m uvicorn nadir.api.server:app --reload --port 8000), then retry. Open the app on that server, not a separate static-file server.`;
    }
    throw new ApiError(detail || `HTTP ${resp.status}`, resp.status);
  }
  return resp.json();
}

export async function getJSON(url, signal) {
  let resp;
  try { resp = await fetch(url, { signal }); }
  catch (e) { if (e.name === "AbortError") throw e; throw new ApiError("Cannot reach the NADIR server. Is it running?", 0); }
  return handle(resp);
}

export async function postJSON(url, body, signal) {
  let resp;
  try { resp = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}), signal }); }
  catch (e) { if (e.name === "AbortError") throw e; throw new ApiError("Cannot reach the NADIR server. Is it running?", 0); }
  return handle(resp);
}

export function cancelJob(id) {
  if (id) fetch(`/api/jobs/${id}`, { method: "DELETE" }).catch(() => {});
}

// Poll a job snapshot until it finishes.  onUpdate(snapshot) on every tick.
export async function pollJob(job, { onUpdate, signal, interval = 350 } = {}) {
  let snap = job;
  const abort = () => cancelJob(job.id);
  signal?.addEventListener("abort", abort, { once: true });
  try {
    while (true) {
      onUpdate?.(snap);
      if (snap.status === "done") return snap;
      if (snap.status === "error") throw new ApiError(snap.error || "Job failed", 500);
      if (snap.status === "cancelled") throw new DOMException("cancelled", "AbortError");
      await new Promise((r, j) => { const t = setTimeout(r, interval); signal?.addEventListener("abort", () => { clearTimeout(t); j(new DOMException("aborted", "AbortError")); }, { once: true }); });
      snap = await getJSON(`/api/jobs/${job.id}`, signal);
    }
  } finally { signal?.removeEventListener("abort", abort); }
}
