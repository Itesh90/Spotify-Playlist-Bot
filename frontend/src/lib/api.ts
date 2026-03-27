export function getApiBase(): string {
  // If explicitly set via env, use it
  if (process.env.NEXT_PUBLIC_API_URL && process.env.NEXT_PUBLIC_API_URL !== "http://localhost:5000") {
    return process.env.NEXT_PUBLIC_API_URL;
  }
  // Auto-detect GitHub Codespaces: swap port 3000 → 5000 in the URL
  if (typeof window !== "undefined" && window.location.hostname.includes(".app.github.dev")) {
    return window.location.origin.replace("-3000.", "-5000.");
  }
  return process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";
}

const API_BASE = getApiBase();

type FetchOptions = {
  method?: string;
  body?: unknown;
};

export async function api<T = unknown>(path: string, opts: FetchOptions = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data as T;
}
