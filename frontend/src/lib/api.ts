const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";

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
