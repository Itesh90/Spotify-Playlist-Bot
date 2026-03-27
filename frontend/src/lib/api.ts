/**
 * API base URL.
 *
 * Returns "" (empty string = same-origin) because Next.js rewrites in
 * next.config.ts proxy all /api/* requests to the Flask backend.
 * This eliminates CORS — the browser only ever talks to port 3000.
 */
export function getApiBase(): string {
  return "";
}

type FetchOptions = {
  method?: string;
  body?: unknown;
};

export async function api<T = unknown>(path: string, opts: FetchOptions = {}): Promise<T> {
  const base = getApiBase();
  const res = await fetch(`${base}${path}`, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data as T;
}

