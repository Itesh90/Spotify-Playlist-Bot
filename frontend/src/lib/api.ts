export function getApiBase(): string {
  // 1. Explicitly set env var (for Oracle / Railway / Render production)
  const envUrl = process.env.NEXT_PUBLIC_API_URL;
  if (envUrl && envUrl !== "http://localhost:5000" && envUrl !== "") {
    return envUrl;
  }

  // 2. GitHub Codespaces: swap port 3000 → 5000 in the forwarded URL
  //    (runs client-side only, window is available here when called from api())
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host.includes(".app.github.dev")) {
      return window.location.origin.replace("-3000.", "-5000.");
    }
    // 3. Same server, different port (Oracle / VPS running both services)
    //    If the hostname is not localhost and no env var set, try port 5000
    if (host !== "localhost" && host !== "127.0.0.1") {
      const proto = window.location.protocol;
      return `${proto}//${host}:5000`;
    }
  }

  // 4. Local dev fallback
  return "http://localhost:5000";
}

type FetchOptions = {
  method?: string;
  body?: unknown;
};

export async function api<T = unknown>(path: string, opts: FetchOptions = {}): Promise<T> {
  // Call getApiBase() here (per-request) so it runs on the client with window available
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
