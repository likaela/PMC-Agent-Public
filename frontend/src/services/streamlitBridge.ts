// Streamlit v1 component protocol. Only enabled in the dedicated cloud build.
type Reply = { id: string; status: number; body: unknown };
type Job = { id: string; method: string; path: string; body?: unknown };
const jobs = new Map<string, Job>();
const waiting = new Map<string, { resolve: (r: Response) => void; reject: (e: unknown) => void; cleanup: () => void }>();
let started = false;
let ready = false;
let owner = "";
const post = (type: string, data: object) => window.parent.postMessage({ isStreamlitMessage: true, type, ...data }, window.location.origin);
const flush = () => { if (ready && jobs.size) post("streamlit:setComponentValue", { dataType: "json", value: { owner, requests: [...jobs.values()] } }); };

export function initializeStreamlitBridge() {
  if (started || typeof window === "undefined") return;
  started = true;
  document.documentElement.dataset.host = "streamlit";
  try { owner = localStorage.getItem("pmc-cloud-browser-v1") || ""; } catch { /* private browsing */ }
  if (!/^[a-f0-9]{64}$/.test(owner)) {
    owner = [...crypto.getRandomValues(new Uint8Array(32))].map(n => n.toString(16).padStart(2, "0")).join("");
    try { localStorage.setItem("pmc-cloud-browser-v1", owner); } catch { /* session-only ownership */ }
  }
  window.addEventListener("message", event => {
    if (event.source !== window.parent || event.origin !== window.location.origin || event.data?.type !== "streamlit:render") return;
    ready = true;
    for (const reply of (event.data.args?.responses || []) as Reply[]) {
      jobs.delete(reply.id);
      const task = waiting.get(reply.id);
      if (!task) continue;
      task.cleanup();
      waiting.delete(reply.id);
      task.resolve(new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } }));
    }
    flush();
  });
  post("streamlit:componentReady", { apiVersion: 1 });
  post("streamlit:setFrameHeight", { height: 900 });
}

export function streamlitRequest(path: string, init: RequestInit): Promise<Response> {
  initializeStreamlitBridge();
  return new Promise((resolve, reject) => {
    const signal = init.signal;
    if (signal?.aborted) { reject(new DOMException("Cancelled", "AbortError")); return; }
    const id = crypto.randomUUID();
    const abort = () => { waiting.get(id)?.cleanup(); waiting.delete(id); reject(new DOMException("Cancelled", "AbortError")); };
    const timer = window.setTimeout(() => { waiting.get(id)?.cleanup(); waiting.delete(id); jobs.delete(id); reject(new Error("Cloud request timed out")); }, 120_000);
    const cleanup = () => { window.clearTimeout(timer); signal?.removeEventListener("abort", abort); };
    waiting.set(id, { resolve, reject, cleanup });
    signal?.addEventListener("abort", abort, { once: true });
    jobs.set(id, { id, method: init.method || "GET", path, ...(init.body ? { body: JSON.parse(String(init.body)) } : {}) });
    flush();
  });
}
