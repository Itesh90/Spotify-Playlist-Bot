export function getApiBase(): string {
  // 1. BROWSER DETECTION FIRST — env vars get baked at Docker build time
  //    so they are unreliable in Codespaces. Check the live URL instead.
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    const origin = window.location.origin;

    // GitHub Codespaces: swap port 3000 → 5000
    if (host.includes(".app.github.dev")) {
      const base = origin.replace("-3000.", "-5000.");
      console.log("[Fleet Sentinel] Codespace detected → API:", base);
      return base;
    }

    // Any non-localhost host (Oracle / VPS): use same host on port 5000
    if (host !== "localhost" && host !== "127.0.0.1") {
      const base = `${window.location.protocol}//${host}:5000`;
      console.log("[Fleet Sentinel] Remote host detected → API:", base);
      return base;
    }
  }

  // 2. Explicit env var (only if NOT the default localhost value)
  const envUrl = process.env.NEXT_PUBLIC_API_URL;
  if (envUrl && envUrl !== "http://localhost:5000" && envUrl !== "") {
    return envUrl;
  }

  // 3. Local dev fallback
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
