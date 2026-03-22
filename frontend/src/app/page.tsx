"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Play, Square, Plus, Trash2, ExternalLink, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

type LogEntry = { time: string; msg: string };

type Account = {
  id: string;
  name: string;
  authorized: boolean;
  playlists: string[];
  status: string;
  current_index: number;
  log: LogEntry[];
};

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [name, setName] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [playlistInputs, setPlaylistInputs] = useState<Record<string, string>>({});

  const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";

  // Check auth
  useEffect(() => {
    api<{ user: string }>("/api/me")
      .then((d) => setUser(d.user))
      .catch(() => router.push("/login"));
  }, [router]);

  // Poll accounts
  const fetchAccounts = useCallback(async () => {
    try {
      const data = await api<Account[]>("/api/accounts");
      setAccounts(data);
    } catch { /* ignore if not authed yet */ }
  }, []);

  useEffect(() => {
    if (!user) return;
    fetchAccounts();
    const interval = setInterval(fetchAccounts, 3000);
    return () => clearInterval(interval);
  }, [user, fetchAccounts]);

  async function addAccount() {
    if (!name || !clientId || !clientSecret) return;
    try {
      await api("/api/add_account", {
        method: "POST",
        body: { name, client_id: clientId, client_secret: clientSecret },
      });
      setName("");
      setClientId("");
      setClientSecret("");
      fetchAccounts();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed");
    }
  }

  async function deleteAccount(id: string) {
    if (!confirm("Delete this account?")) return;
    await api(`/api/delete_account/${id}`, { method: "DELETE" });
    fetchAccounts();
  }

  async function addPlaylist(accountId: string) {
    const uri = playlistInputs[accountId]?.trim();
    if (!uri) return;
    try {
      await api(`/api/add_playlist/${accountId}`, {
        method: "POST",
        body: { uri },
      });
      setPlaylistInputs((p) => ({ ...p, [accountId]: "" }));
      fetchAccounts();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed");
    }
  }

  async function removePlaylist(accountId: string, index: number) {
    await api(`/api/remove_playlist/${accountId}/${index}`, { method: "DELETE" });
    fetchAccounts();
  }

  async function startBot(id: string) {
    await api(`/api/start/${id}`, { method: "POST" });
    fetchAccounts();
  }

  async function stopBot(id: string) {
    await api(`/api/stop/${id}`, { method: "POST" });
    fetchAccounts();
  }

  async function startAll() {
    await api("/api/start-all", { method: "POST" });
    fetchAccounts();
  }

  async function stopAll() {
    await api("/api/stop-all", { method: "POST" });
    fetchAccounts();
  }

  async function logout() {
    await api("/api/logout", { method: "POST" });
    router.push("/login");
  }

  function statusColor(status: string) {
    if (status === "running" || status === "playing") return "bg-emerald-400";
    if (status === "error") return "bg-red-400";
    if (status === "starting") return "bg-yellow-400";
    return "bg-white/30";
  }

  if (!user) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <RefreshCw className="animate-spin text-brand-cyan" size={32} />
      </main>
    );
  }

  return (
    <main className="min-h-screen relative z-10 p-4 md:p-8 lg:p-12">
      {/* Header */}
      <header className="glass-panel flex flex-wrap items-center justify-between p-4 md:px-8 rounded-2xl mb-8 gap-4">
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-brand-cyan to-brand-blue flex items-center justify-center text-xl shadow-[0_4px_15px_rgba(0,242,254,0.3)]">
            🎵
          </div>
          <h1 className="text-xl md:text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-white to-gray-400">
            Spotify Playlist <span className="text-gradient">Bot</span>
          </h1>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <p className="text-sm text-white/70 font-medium mr-2">Hi, {user}</p>
          <button onClick={logout} className="px-4 py-2 rounded-xl text-sm font-semibold bg-white/5 border border-white/10 hover:border-brand-cyan hover:bg-brand-cyan/10 transition-all">
            Logout
          </button>
          <button onClick={startAll} className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-black bg-gradient-to-r from-brand-cyan to-brand-blue shadow-[0_4px_15px_rgba(0,242,254,0.3)] hover:-translate-y-0.5 transition-all">
            <Play size={14} fill="currentColor" /> Start All
          </button>
          <button onClick={stopAll} className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-brand-pink to-brand-purple shadow-[0_4px_15px_rgba(255,0,127,0.3)] hover:-translate-y-0.5 transition-all">
            <Square size={14} fill="currentColor" /> Stop All
          </button>
        </div>
      </header>

      {/* Add Account */}
      <section className="glass-panel p-6 rounded-2xl mb-8">
        <h2 className="text-lg font-semibold flex items-center gap-2 mb-5 text-white/90">
          <Plus size={20} className="text-brand-cyan" /> Add Account
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
          <div className="space-y-1">
            <label className="text-xs font-semibold text-white/50 uppercase tracking-widest">Account Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My Account" className="w-full px-4 py-3 bg-black/20 border border-white/10 rounded-xl focus:outline-none focus:border-brand-cyan transition-all text-sm" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-semibold text-white/50 uppercase tracking-widest">Client ID</label>
            <input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="Spotify Client ID" className="w-full px-4 py-3 bg-black/20 border border-white/10 rounded-xl focus:outline-none focus:border-brand-cyan transition-all text-sm" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-semibold text-white/50 uppercase tracking-widest">Client Secret</label>
            <input value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} type="password" placeholder="Spotify Client Secret" className="w-full px-4 py-3 bg-black/20 border border-white/10 rounded-xl focus:outline-none focus:border-brand-cyan transition-all text-sm" />
          </div>
          <button onClick={addAccount} className="w-full px-4 py-3 rounded-xl text-sm font-semibold text-black bg-gradient-to-r from-brand-cyan to-brand-blue shadow-[0_4px_15px_rgba(0,242,254,0.3)] hover:-translate-y-0.5 transition-all">Add</button>
        </div>
      </section>

      {/* Account Cards */}
      {accounts.length === 0 ? (
        <section className="glass-panel rounded-2xl p-16 flex flex-col items-center justify-center text-center text-white/50">
          <div className="text-5xl mb-4">🎧</div>
          <p className="text-lg font-medium">No accounts yet — add one above to get started</p>
        </section>
      ) : (
        <section className="grid grid-cols-1 gap-6">
          {accounts.map((acc) => (
            <div key={acc.id} className="glass-panel rounded-2xl overflow-hidden">
              {/* Card Header */}
              <div className="flex items-center justify-between p-5 border-b border-white/5">
                <div className="flex items-center gap-3">
                  <div className={`w-2.5 h-2.5 rounded-full ${statusColor(acc.status)}`} />
                  <h3 className="font-semibold text-lg">{acc.name}</h3>
                  <span className="text-xs text-white/40 uppercase tracking-wide">{acc.status}</span>
                </div>
                <div className="flex items-center gap-2">
                  {!acc.authorized ? (
                    <a href={`${API_BASE}/authorize/${acc.id}`} className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-yellow-500/20 text-yellow-300 border border-yellow-500/30 hover:bg-yellow-500/30 transition-all flex items-center gap-1.5">
                      <ExternalLink size={12} /> Authorize
                    </a>
                  ) : (
                    <span className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">✓ Authorized</span>
                  )}
                  {acc.status === "idle" || acc.status === "error" ? (
                    <button onClick={() => startBot(acc.id)} className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-cyan/20 text-brand-cyan border border-brand-cyan/30 hover:bg-brand-cyan/30 transition-all">▶ Start</button>
                  ) : (
                    <button onClick={() => stopBot(acc.id)} className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-pink/20 text-brand-pink border border-brand-pink/30 hover:bg-brand-pink/30 transition-all">■ Stop</button>
                  )}
                  <button onClick={() => deleteAccount(acc.id)} className="w-8 h-8 flex items-center justify-center rounded-lg bg-white/5 border border-white/10 text-white/50 hover:text-red-400 hover:border-red-400/50 transition-all">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {/* Playlists */}
              <div className="p-5 border-b border-white/5">
                <h4 className="text-xs font-semibold text-white/50 uppercase tracking-widest mb-3">Playlists ({acc.playlists.length})</h4>
                {acc.playlists.length > 0 && (
                  <div className="space-y-1.5 mb-3">
                    {acc.playlists.map((p, i) => (
                      <div key={i} className={`flex items-center justify-between px-3 py-2 rounded-lg text-sm ${i === acc.current_index && acc.status !== "idle" ? "bg-brand-cyan/10 border border-brand-cyan/20 text-brand-cyan" : "bg-black/10 text-white/60"}`}>
                        <span className="truncate flex-1"><span className="text-white/30 mr-2 text-xs">{i + 1}.</span>{p}</span>
                        <button onClick={() => removePlaylist(acc.id, i)} className="ml-2 text-white/30 hover:text-red-400 transition-colors"><Trash2 size={12} /></button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex gap-2">
                  <input
                    value={playlistInputs[acc.id] || ""}
                    onChange={(e) => setPlaylistInputs((p) => ({ ...p, [acc.id]: e.target.value }))}
                    onKeyDown={(e) => e.key === "Enter" && addPlaylist(acc.id)}
                    placeholder="Paste one or multiple playlist URLs (space or newline separated)"
                    className="flex-1 px-3 py-2 bg-black/20 border border-white/10 rounded-lg focus:outline-none focus:border-brand-cyan transition-all text-sm"
                  />
                  <button onClick={() => addPlaylist(acc.id)} className="px-4 py-2 rounded-lg text-xs font-semibold bg-white/5 border border-white/10 hover:border-brand-cyan hover:bg-brand-cyan/10 transition-all">+ Add</button>
                </div>
              </div>

              {/* Activity Log */}
              <div className="p-5">
                <h4 className="text-xs font-semibold text-white/50 uppercase tracking-widest mb-3">Activity Log</h4>
                <div className="bg-black/20 rounded-lg p-3 max-h-32 overflow-y-auto font-mono text-xs space-y-0.5">
                  {acc.log && acc.log.length > 0 ? (
                    acc.log.slice(0, 20).map((entry, i) => (
                      <div key={i} className="text-white/50">
                        <span className="text-brand-cyan mr-2">{entry.time}</span>
                        {entry.msg}
                      </div>
                    ))
                  ) : (
                    <p className="text-white/30 italic">No activity yet</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </section>
      )}
    </main>
  );
}
