"use client";

import { useEffect, useState, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, RefreshCw, Maximize, Minimize } from "lucide-react";
import { api, getApiBase } from "@/lib/api";

export default function BrowserWindow() {
  const params = useParams();
  const router = useRouter();
  const accountId = params.id as string;
  const imgRef = useRef<HTMLImageElement>(null);

  const [accountName, setAccountName] = useState<string>("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(0);
  const [connected, setConnected] = useState(false);

  const API_BASE = getApiBase();

  // Fetch account name
  useEffect(() => {
    api<{ id: string; name: string }[]>("/api/accounts")
      .then((accounts) => {
        const acc = accounts.find((a) => a.id === accountId);
        if (acc) setAccountName(acc.name);
      })
      .catch(() => router.push("/login"));
  }, [accountId, router]);

  // Refresh screenshot every 1 second for near-realtime feed
  useEffect(() => {
    setLastUpdate(Date.now());
    const interval = setInterval(() => {
      setLastUpdate(Date.now());
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen();
      setIsFullscreen(true);
    } else {
      document.exitFullscreen();
      setIsFullscreen(false);
    }
  };

  return (
    <main className="h-screen bg-[#050505] flex flex-col overflow-hidden font-mono">
      {/* Header Bar */}
      <header className="flex items-center justify-between px-4 py-2 bg-black/80 border-b border-brand-cyan/20 backdrop-blur-md shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push("/")}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors text-white/60 hover:text-white"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="w-2 h-2 rounded-full bg-brand-cyan animate-pulse" />
          <span className="text-sm font-bold text-white">
            {accountName || accountId}
          </span>
          <span className="text-[10px] uppercase tracking-widest text-brand-cyan/60 px-2 py-0.5 rounded bg-brand-cyan/10 border border-brand-cyan/20">
            Live Browser Feed
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span className={`text-[10px] px-2 py-1 rounded ${connected ? "text-green-400 bg-green-500/10" : "text-red-400 bg-red-500/10"}`}>
            {connected ? "CONNECTED" : "NO SIGNAL"}
          </span>
          <button
            onClick={toggleFullscreen}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors text-white/60 hover:text-white"
          >
            {isFullscreen ? <Minimize size={16} /> : <Maximize size={16} />}
          </button>
        </div>
      </header>

      {/* Live Screenshot Feed */}
      <div className="flex-1 flex items-center justify-center bg-black p-2 min-h-0">
        {lastUpdate > 0 ? (
          <img
            ref={imgRef}
            src={`${API_BASE}/api/v2/screen/${accountId}?t=${lastUpdate}`}
            alt={`Live browser feed — ${accountName}`}
            className="max-w-full max-h-full object-contain rounded-lg"
            onError={() => setConnected(false)}
            onLoad={() => setConnected(true)}
          />
        ) : (
          <span className="text-white/30 text-sm font-mono">Connecting...</span>
        )}
      </div>
    </main>
  );
}
