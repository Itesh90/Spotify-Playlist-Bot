"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Play, Square, Plus, Trash2, ExternalLink, RefreshCw, Terminal, MonitorSmartphone, Globe, Cpu, Activity, Info, RotateCcw, ShieldCheck, Monitor, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { api, getApiBase } from "@/lib/api";

type LogEntry = { time: string; msg: string } | string;

type Account = {
  id: string;
  name: string;
  authorized: boolean;
  playlists: string[];
  status: string;
  current_index: number;
  log?: LogEntry[];
};

type FleetStatus = {
  id: string;
  name: string;
  authorized: boolean;
  playlists: number;
  current_index: number;
  status: string;
  docker_available: boolean;
  container_running: boolean;
  proxy_url?: string;
  spotify_user?: string | null;
};

// Combined type
type MergedAccount = Account & {
  container_running: boolean;
  docker_available: boolean;
  live_logs: string[];
  proxy_url?: string;
  spotify_user?: string | null;
};

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<MergedAccount[]>([]);
  const [name, setName] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [proxyUrl, setProxyUrl] = useState("");
  const [playlistInputs, setPlaylistInputs] = useState<Record<string, string>>({});
  const [expandedLogs, setExpandedLogs] = useState<Record<string, boolean>>({});
  const [setupModal, setSetupModal] = useState<{
    accountId: string;
    accountName: string;
    vncUrl: string;
  } | null>(null);
  const [setupStatus, setSetupStatus] = useState<string>("");

  const API_BASE = getApiBase();

  // Auth
  useEffect(() => {
    api<{ user: string }>("/api/me")
      .then((d) => setUser(d.user))
      .catch(() => router.push("/login"));
  }, [router]);

  // Main Polling
  const fetchAccounts = useCallback(async () => {
    try {
      const [accData, fleetData] = await Promise.all([
        api<Account[]>("/api/accounts").catch(() => []),
        api<FleetStatus[]>("/api/v2/fleet").catch(() => []),
      ]);

      const merged = accData.map((acc) => {
        const f = fleetData.find((fd) => fd.id === acc.id);
        return {
          ...acc,
          container_running: f?.container_running || false,
          docker_available: f?.docker_available || false,
          status: f?.status || acc.status,
          proxy_url: f?.proxy_url || "",
          spotify_user: f?.spotify_user || null,
          live_logs: [],
        };
      });
      setAccounts(merged);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (!user) return;
    fetchAccounts();
    const interval = setInterval(fetchAccounts, 3000);
    return () => clearInterval(interval);
  }, [user, fetchAccounts]);

  // Real-time live logs
  useEffect(() => {
    if (!user) return;
    const fetchLogs = async () => {
      setAccounts((prev) => {
        const next = [...prev];
        next.forEach(async (acc, i) => {
          if (acc.container_running || acc.status === "setup") {
            try {
              const data = await api<{ logs: string[] }>(`/api/v2/logs/${acc.id}?tail=15`);
              setAccounts((curr) => {
                const c = [...curr];
                if (c[i]) c[i].live_logs = data.logs;
                return c;
              });
            } catch {}
          }
        });
        return next;
      });
    };
    const logInterval = setInterval(fetchLogs, 2000);
    return () => clearInterval(logInterval);
  }, [user]);

  // Account Operations
  async function addAccount() {
    if (!name || !clientId || !clientSecret) return;
    try {
      await api("/api/add_account", {
        method: "POST",
        body: { name, client_id: clientId, client_secret: clientSecret, proxy_url: proxyUrl },
      });
      setName(""); setClientId(""); setClientSecret(""); setProxyUrl(""); fetchAccounts();
    } catch (err: any) { alert(err.message || "Failed"); }
  }

  async function deleteAccount(id: string) {
    if (!confirm("Delete this isolated node?")) return;
    await api(`/api/delete_account/${id}`, { method: "DELETE" });
    fetchAccounts();
  }

  async function addPlaylist(accountId: string) {
    const uri = playlistInputs[accountId]?.trim();
    if (!uri) return;
    await api(`/api/add_playlist/${accountId}`, { method: "POST", body: { uri } });
    setPlaylistInputs((p) => ({ ...p, [accountId]: "" }));
    fetchAccounts();
  }

  async function removePlaylist(accountId: string, index: number) {
    await api(`/api/remove_playlist/${accountId}/${index}`, { method: "DELETE" });
    fetchAccounts();
  }

  // V2 Orchestration Operations
  async function startNode(id: string) {
    await api(`/api/v2/start/${id}`, { method: "POST" });
    fetchAccounts();
  }

  async function stopNode(id: string) {
    await api(`/api/v2/stop/${id}`, { method: "POST" });
    fetchAccounts();
  }

  async function setupLogin(id: string, accountName: string) {
    try {
      const d = await api<{ok: boolean; vnc_url: string; port: number}>(`/api/v2/setup/${id}`, { method: "POST" });
      if (d.vnc_url) {
        setSetupModal({ accountId: id, accountName, vncUrl: d.vnc_url });
        setSetupStatus("waiting");
      } else {
        alert("Setup started but no VNC URL returned. Check Docker logs.");
      }
      fetchAccounts();
    } catch (err: any) {
      alert(err.message || "Failed to start setup container");
    }
  }

  // Poll setup status when modal is open
  useEffect(() => {
    if (!setupModal) return;
    const interval = setInterval(async () => {
      try {
        const d = await api<{status: string; spotify_user?: string}>(`/api/v2/session_status/${setupModal.accountId}`);
        if (d.status === "done") {
          setSetupStatus("done");
          setTimeout(() => {
            setSetupModal(null);
            setSetupStatus("");
            fetchAccounts();
          }, 2000);
          clearInterval(interval);
        }
      } catch {}
    }, 3000);
    return () => clearInterval(interval);
  }, [setupModal, fetchAccounts]);

  async function stopAllNodes() {
    for (const acc of accounts) {
      if (acc.container_running || acc.status !== "idle") {
        await api(`/api/v2/stop/${acc.id}`, { method: "POST" }).catch(()=>{});
      }
    }
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

  async function resetQueue(id: string) {
    if (!confirm("Reset queue to beginning? This will stop the bot if running.")) return;
    await api(`/api/reset_queue/${id}`, { method: "POST" });
    fetchAccounts();
  }

  async function reauthorize(id: string) {
    try {
      const data = await api<{ok: boolean; auth_url: string}>(`/api/reauthorize/${id}`, { method: "POST" });
      if (data.auth_url) {
        window.open(data.auth_url, "_blank");
      }
      fetchAccounts();
    } catch (err: any) { alert(err.message || "Re-authorize failed"); }
  }

  const toggleLogs = (id: string) => setExpandedLogs(p => ({ ...p, [id]: !p[id] }));

  if (!user) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <RefreshCw className="animate-spin text-brand-cyan" size={48} />
      </main>
    );
  }

  return (
    <main className="min-h-screen relative z-10 p-4 md:p-8 max-w-7xl mx-auto">
      {/* Header */}
      <motion.header 
        initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }}
        className="glass-panel flex flex-col md:flex-row items-center justify-between p-6 rounded-2xl mb-8 gap-4 border-b-2 border-b-brand-cyan/30"
      >
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-brand-cyan to-brand-blue flex items-center justify-center shadow-[0_0_20px_rgba(0,242,254,0.5)]">
            <Activity className="text-white" size={24} />
          </div>
          <div>
            <h1 className="text-2xl md:text-3xl font-bold text-white tracking-tight">Fleet <span className="text-gradient">Command Center</span></h1>
            <p className="text-brand-cyan/70 text-sm font-mono tracking-widest uppercase">Multi-Container Automation</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button onClick={() => router.push("/mainframe")} className="px-4 py-2 rounded-xl text-sm font-semibold border border-brand-cyan/50 text-brand-cyan hover:bg-brand-cyan/10 transition-all flex items-center gap-2">
            <MonitorSmartphone size={16} /> Open Mainframe
          </button>
          <button onClick={() => api("/api/logout", { method: "POST" }).then(()=>router.push("/login"))} className="px-4 py-2 rounded-xl text-sm font-semibold bg-white/5 border border-white/10 hover:border-white/30 transition-all">Logout</button>
          <button onClick={stopAllNodes} className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold text-white shadow-[0_0_15px_rgba(255,0,127,0.4)] bg-gradient-to-r from-brand-pink to-brand-purple hover:scale-105 transition-all">
            <Square size={16} fill="currentColor" /> Global Kill
          </button>
        </div>
      </motion.header>

      {/* Node Registration */}
      <motion.section 
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}
        className="glass-panel p-6 rounded-2xl mb-8 relative overflow-hidden"
      >
        <div className="absolute top-0 right-0 w-64 h-64 bg-brand-purple/10 rounded-full blur-3xl" />
        <h2 className="text-lg font-bold flex items-center gap-2 mb-4 text-white">
          <Plus size={20} className="text-brand-cyan" /> Deploy New Node
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4 items-end relative z-10">
          <div className="space-y-1">
            <label className="text-xs font-semibold text-brand-cyan uppercase tracking-widest">Node Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} className="w-full px-4 py-3 border border-white/10 rounded-xl focus:outline-none focus:border-brand-cyan transition-all text-sm bg-black/40 text-white placeholder-white/30" placeholder="e.g. Acc-01" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-semibold text-brand-cyan uppercase tracking-widest">Client ID</label>
            <input value={clientId} onChange={(e) => setClientId(e.target.value)} className="w-full px-4 py-3 border border-white/10 rounded-xl focus:outline-none focus:border-brand-cyan transition-all text-sm bg-black/40 text-white placeholder-white/30" placeholder="Spotify App ID" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-semibold text-brand-cyan uppercase tracking-widest">Client Secret</label>
            <input value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} type="password" className="w-full px-4 py-3 border border-white/10 rounded-xl focus:outline-none focus:border-brand-cyan transition-all text-sm bg-black/40 text-white placeholder-white/30" placeholder="Spotify App Secret" />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-semibold text-brand-cyan uppercase tracking-widest">Proxy URL <span className="text-white/30">(optional)</span></label>
            <input value={proxyUrl} onChange={(e) => setProxyUrl(e.target.value)} className="w-full px-4 py-3 border border-white/10 rounded-xl focus:outline-none focus:border-brand-cyan transition-all text-sm bg-black/40 text-white placeholder-white/30" placeholder="http://user:pass@host:port" />
          </div>
          <button onClick={addAccount} className="w-full px-4 py-3 rounded-xl text-sm font-bold text-black bg-gradient-to-r from-brand-cyan to-brand-blue shadow-[0_0_15px_rgba(0,242,254,0.3)] hover:scale-105 transition-all">Initialize Node</button>
        </div>
      </motion.section>

      {/* Fleet Grid */}
      {accounts.length === 0 ? (
        <div className="glass-panel p-20 rounded-2xl text-center text-white/40 font-mono">
          <MonitorSmartphone size={48} className="mx-auto mb-4 opacity-50" />
          NO CONTAINERS DEPLOYED
        </div>
      ) : (
        <motion.div className="grid grid-cols-1 lg:grid-cols-2 gap-6" layout>
          <AnimatePresence>
            {accounts.map((acc, idx) => {
              const isActive = acc.container_running || acc.status === "playing";
              const isError = acc.status === "error";
              
              return (
                <motion.div 
                  key={acc.id} 
                  initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }} transition={{ delay: idx * 0.05 }}
                  className={`glass-panel rounded-2xl overflow-hidden border-2 transition-colors duration-500 ${isActive ? 'border-brand-cyan/40 shadow-[0_0_30px_rgba(0,242,254,0.1)]' : isError ? 'border-brand-pink/40 shadow-[0_0_30px_rgba(255,0,127,0.1)]' : 'border-white/5'}`}
                >
                  {/* Pod Header */}
                  <div className="p-5 border-b border-white/10 flex justify-between items-center bg-black/20">
                    <div className="flex items-center gap-3">
                      {/* Pulse ring */}
                      <div className="relative flex h-3 w-3">
                        {isActive && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand-cyan opacity-75"></span>}
                        <span className={`relative inline-flex rounded-full h-3 w-3 ${isActive ? 'bg-brand-cyan' : isError ? 'bg-brand-pink' : 'bg-white/20'}`}></span>
                      </div>
                      <h3 className="font-bold text-lg text-white">{acc.name}</h3>
                      {acc.spotify_user && (
                        <span className="text-xs text-green-400 font-mono">♫ {acc.spotify_user}</span>
                      )}
                      <div className="flex gap-2">
                        <span className="px-2 py-0.5 rounded-md bg-white/5 border border-white/10 text-[10px] uppercase font-mono tracking-wider text-brand-purple flex items-center gap-1">
                          <Cpu size={10} /> Node-{idx+1}
                        </span>
                        {acc.docker_available && (
                          <span className="px-2 py-0.5 rounded-md bg-blue-500/10 border border-blue-500/20 text-[10px] uppercase font-mono text-blue-400">Docker API</span>
                        )}
                      </div>
                    </div>
                    <button onClick={() => deleteAccount(acc.id)} className="text-white/30 hover:text-brand-pink transition-colors"><Trash2 size={16} /></button>
                  </div>

                  <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-4 border-b border-white/5">
                    {/* Controls */}
                    <div className="space-y-4">
                      <div className="flex items-center justify-between">
                        <span className="text-xs uppercase tracking-widest text-white/50 font-semibold">Engine State</span>
                        <span className={`text-xs font-mono uppercase ${isActive ? 'text-brand-cyan' : 'text-white/40'}`}>{acc.status}</span>
                      </div>
                      
                      <div className="flex flex-col gap-2">
                        {!acc.authorized && (
                          <a href={`${API_BASE}/authorize/${acc.id}`} className="w-full text-center px-4 py-2 rounded-xl text-sm font-bold bg-yellow-500/20 text-yellow-300 border border-yellow-500/40 hover:bg-yellow-500/30 transition-all shadow-[0_0_15px_rgba(234,179,8,0.2)]">1. OAuth Auth</a>
                        )}
                        
                        {!isActive ? (
                          <>
                            <div className="flex gap-2">
                              <button onClick={() => setupLogin(acc.id, acc.name)} className="flex-1 px-3 py-2 rounded-xl text-xs font-bold bg-brand-purple/20 text-brand-purple border border-brand-purple/40 hover:bg-brand-purple/30 transition-all flex items-center justify-center gap-2">
                                <Monitor size={14} /> Setup Login
                              </button>
                              <button onClick={() => startNode(acc.id)} disabled={!acc.authorized} className="flex-1 px-3 py-2 rounded-xl text-xs font-bold bg-brand-cyan/20 text-brand-cyan border border-brand-cyan/40 hover:bg-brand-cyan/30 disabled:opacity-30 transition-all flex items-center justify-center gap-2">
                                <Play size={14} fill="currentColor" /> Docker Node
                              </button>
                            </div>
                            <div className="flex gap-2">
                              <button onClick={() => startBot(acc.id)} disabled={!acc.authorized} className="flex-1 px-3 py-2 rounded-xl text-xs font-bold bg-green-500/20 text-green-400 border border-green-500/40 hover:bg-green-500/30 disabled:opacity-30 transition-all flex items-center justify-center gap-2">
                                <Play size={14} /> Start Bot
                              </button>
                              <button onClick={() => resetQueue(acc.id)} className="flex-1 px-3 py-2 rounded-xl text-xs font-bold bg-orange-500/20 text-orange-400 border border-orange-500/40 hover:bg-orange-500/30 transition-all flex items-center justify-center gap-2">
                                <RotateCcw size={14} /> Restart Queue
                              </button>
                            </div>
                            <button onClick={() => reauthorize(acc.id)} className="w-full px-3 py-2 rounded-xl text-xs font-bold bg-amber-500/10 text-amber-400 border border-amber-500/30 hover:bg-amber-500/20 transition-all flex items-center justify-center gap-2">
                              <ShieldCheck size={14} /> Re-Authorize
                            </button>
                          </>
                        ) : (
                          <div className="flex gap-2">
                            <button onClick={() => stopBot(acc.id)} className="flex-1 px-4 py-2 rounded-xl text-sm font-bold bg-brand-pink/20 text-brand-pink border border-brand-pink/40 hover:bg-brand-pink/30 hover:shadow-[0_0_15px_rgba(255,0,127,0.3)] transition-all flex items-center justify-center gap-2">
                              <Square size={14} fill="currentColor" /> Stop Bot
                            </button>
                            <button onClick={() => stopNode(acc.id)} className="flex-1 px-4 py-2 rounded-xl text-sm font-bold bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30 transition-all flex items-center justify-center gap-2">
                              <Square size={14} fill="currentColor" /> Kill Node
                            </button>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Network & Identity */}
                    <div className="bg-black/30 rounded-xl p-4 border border-white/5 flex flex-col justify-between">
                      <div className="flex items-center gap-2 mb-2 text-brand-blue">
                        <Globe size={16} /> <span className="text-xs font-bold uppercase tracking-wider">Network Isolation</span>
                      </div>
                      <div className="text-sm font-mono text-white/80">
                        {acc.proxy_url ? (
                          <span className="text-brand-cyan text-xs">{acc.proxy_url.replace(/\/\/([^:]+):([^@]+)@/, '//$1:***@')}</span>
                        ) : (
                          <span className="text-white/40">Direct (No Proxy)</span>
                        )}
                      </div>
                      <div className="mt-2 flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${isActive ? 'bg-brand-cyan shadow-[0_0_8px_#00f2fe]' : 'bg-white/20'}`} />
                        <span className="text-xs text-white/50">{isActive ? 'Tunnel Active' : 'Disconnected'}</span>
                      </div>
                    </div>
                  </div>

                  {/* Playlists */}
                  <div className="p-5 bg-black/10">
                    <div className="flex justify-between items-center mb-3">
                      <h4 className="text-xs font-bold text-white/50 uppercase tracking-widest">Queue ({acc.playlists.length})</h4>
                    </div>
                    {acc.playlists.length > 0 && (
                      <div className="space-y-2 mb-4 max-h-32 overflow-y-auto pr-2 custom-scrollbar">
                        {acc.playlists.map((p, i) => (
                          <div key={i} className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs font-mono border ${i === acc.current_index && isActive ? "bg-brand-cyan/10 border-brand-cyan/30 text-brand-cyan" : "bg-black/30 border-white/5 text-white/60"}`}>
                            <span className="truncate flex-1"><span className="opacity-50 mr-2">{i + 1}.</span>{p.split("/").pop()?.split("?")[0] || p}</span>
                            <button onClick={() => removePlaylist(acc.id, i)} className="ml-2 opacity-50 hover:opacity-100 hover:text-brand-pink"><Trash2 size={12} /></button>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="flex gap-2">
                      <input value={playlistInputs[acc.id] || ""} onChange={(e) => setPlaylistInputs((p) => ({ ...p, [acc.id]: e.target.value }))} onKeyDown={(e) => e.key === "Enter" && addPlaylist(acc.id)} placeholder="Paste Spotify Playlist URI..." className="flex-1 px-3 py-2 bg-black/30 border border-white/10 rounded-lg focus:outline-none focus:border-brand-cyan transition-all text-xs text-white" />
                      <button onClick={() => addPlaylist(acc.id)} className="px-3 py-2 rounded-lg text-xs font-bold bg-white/10 hover:bg-white/20 transition-all text-white"><Plus size={14} /></button>
                    </div>
                  </div>

                  {/* Live Terminal */}
                  <div className="border-t border-white/5">
                    <button onClick={() => toggleLogs(acc.id)} className="w-full flex items-center justify-between p-3 text-xs font-mono text-white/40 hover:text-white/80 bg-black/40 hover:bg-black/60 transition-colors">
                      <span className="flex items-center gap-2"><Terminal size={12} /> Console Output</span>
                      <span>{expandedLogs[acc.id] ? 'COLLAPSE' : 'EXPAND'}</span>
                    </button>
                    <AnimatePresence>
                      {expandedLogs[acc.id] && (
                        <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }} className="overflow-hidden bg-[#0a0a0a]">
                          <div className="p-4 font-mono text-[10px] md:text-xs leading-relaxed max-h-48 overflow-y-auto custom-scrollbar">
                            {acc.live_logs && acc.live_logs.length > 0 ? (
                              acc.live_logs.map((log, i) => <div key={i} className="text-green-400 opacity-80 mb-1 border-b border-white/5 pb-1">{log}</div>)
                            ) : (
                              acc.log && (acc.log as Array<any>).length > 0 ? (
                                (acc.log as Array<any>).map((entry, i) => (
                                  <div key={i} className="text-white/60 mb-1 border-b border-white/5 pb-1">
                                    <span className="text-brand-cyan mr-2">{typeof entry === 'string' ? '' : entry.time}</span>
                                    {typeof entry === 'string' ? entry : entry.msg}
                                  </div>
                                ))
                              ) : <span className="text-white/30">Awaiting telemetry...</span>
                            )}
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </motion.div>
      )}

      {/* Global CSS overrides inside component for convenience if globals.css doesn't have custom-scrollbar */}
      <style dangerouslySetInnerHTML={{__html: `
        .custom-scrollbar::-webkit-scrollbar { width: 4px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: rgba(0,0,0,0.2); }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(0, 242, 254, 0.5); }
      `}} />

      {/* ── VNC Setup Modal ─────────────────────────────────────────────── */}
      <AnimatePresence>
        {setupModal && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
            onClick={() => setSetupModal(null)}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }}
              className="bg-[#111] border border-white/10 rounded-2xl overflow-hidden shadow-[0_0_60px_rgba(0,242,254,0.15)] w-full max-w-[1400px] max-h-[90vh] flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Modal Header */}
              <div className="flex items-center justify-between p-4 border-b border-white/10 bg-black/40">
                <div className="flex items-center gap-3">
                  <Monitor className="text-brand-purple" size={20} />
                  <div>
                    <h3 className="text-lg font-bold text-white">Setting up <span className="text-brand-cyan">{setupModal.accountName}</span></h3>
                    <p className="text-xs text-white/40 font-mono">
                      {setupStatus === "done" ? (
                        <span className="text-green-400">✅ Login detected! Session saved. Starting headless bot...</span>
                      ) : (
                        <span className="animate-pulse">⏳ Waiting for Spotify login...</span>
                      )}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setSetupModal(null)}
                  className="p-2 rounded-lg hover:bg-white/10 transition-colors text-white/40 hover:text-white"
                >
                  <X size={20} />
                </button>
              </div>

              {/* noVNC iframe */}
              <div className="flex-1 bg-black min-h-[600px]">
                <iframe
                  src={setupModal.vncUrl}
                  className="w-full h-full min-h-[600px] border-0"
                  allow="clipboard-write; clipboard-read"
                  title={`VNC Setup - ${setupModal.accountName}`}
                />
              </div>

              {/* Modal Footer */}
              <div className="flex items-center justify-between p-3 border-t border-white/10 bg-black/40">
                <span className="text-xs text-white/30 font-mono">Port: {setupModal.vncUrl.match(/-(\d+)\./)?.[1] || 'N/A'} | Auto-detect active</span>
                <button
                  onClick={() => setSetupModal(null)}
                  className="px-4 py-2 rounded-xl text-sm font-bold bg-brand-pink/20 text-brand-pink border border-brand-pink/40 hover:bg-brand-pink/30 transition-all"
                >
                  Cancel Setup
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  );
}
