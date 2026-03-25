"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Activity, LayoutGrid, Monitor, ShieldAlert, Cpu } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";

type FleetStatus = {
  id: string;
  name: string;
  status: string;
  container_running: boolean;
};

export default function Mainframe() {
  const router = useRouter();
  const [fleet, setFleet] = useState<FleetStatus[]>([]);
  const [timestamp, setTimestamp] = useState(Date.now());
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";

  // Auth check
  useEffect(() => {
    api<{ user: string }>("/api/me").catch(() => router.push("/login"));
  }, [router]);

  // Poll Fleet Status
  const fetchFleet = useCallback(async () => {
    try {
      const data = await api<FleetStatus[]>("/api/v2/fleet");
      setFleet(data);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchFleet();
    const interval = setInterval(() => {
      fetchFleet();
      setTimestamp(Date.now()); // Force image refresh every 3 seconds
    }, 3000);
    return () => clearInterval(interval);
  }, [fetchFleet]);

  const activeNodes = fleet.filter(f => f.container_running || f.status === "setup");

  return (
    <main className="min-h-screen bg-[#050505] relative z-10 p-4 overflow-hidden font-mono">
      {/* Grid Scanline Overlay */}
      <div className="absolute inset-0 pointer-events-none z-50 opacity-10" 
           style={{ backgroundImage: 'repeating-linear-gradient(transparent, transparent 2px, #000 2px, #000 4px)' }} />

      {/* Header */}
      <header className="flex items-center justify-between p-4 border-b border-brand-cyan/20 mb-6 bg-black/50 backdrop-blur-md rounded-xl shadow-[0_0_20px_rgba(0,242,254,0.1)]">
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 rounded-lg bg-brand-cyan/10 border border-brand-cyan flex items-center justify-center animate-pulse">
            <LayoutGrid className="text-brand-cyan" size={20} />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white tracking-widest uppercase flex items-center gap-2">
              <span className="text-brand-cyan">O.W.L.</span> Mainframe
            </h1>
            <p className="text-brand-cyan/50 text-[10px] uppercase tracking-widest flex items-center gap-2">
              <Activity size={10} /> Live Node Telemetry
            </p>
          </div>
        </div>
        
        <div className="flex items-center gap-4 text-xs">
          <div className="px-3 py-1.5 rounded bg-white/5 border border-white/10 text-white/50 flex items-center gap-2">
            <Cpu size={14} /> Active Nodes: <span className="text-brand-cyan font-bold">{activeNodes.length}</span> / {fleet.length}
          </div>
          <button onClick={() => router.push("/")} className="px-4 py-2 rounded bg-brand-cyan/20 border border-brand-cyan/40 text-brand-cyan font-bold hover:bg-brand-cyan/40 transition-colors uppercase tracking-widest">
            Back to Command
          </button>
        </div>
      </header>

      {/* Camera Grid */}
      {activeNodes.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-[70vh] text-brand-cyan/30 opacity-50">
          <Monitor size={64} className="mb-4" />
          <h2 className="text-xl uppercase tracking-[0.5em]">No Active Transmissions</h2>
          <p className="text-xs mt-2 font-sans opacity-60">Deploy nodes from the Command Center to establish uplink.</p>
        </div>
      ) : (
        <motion.div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6" layout>
          <AnimatePresence>
            {activeNodes.map((node) => (
              <motion.div 
                key={node.id}
                initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }}
                className="relative rounded-xl overflow-hidden border border-brand-cyan/30 shadow-[0_0_15px_rgba(0,242,254,0.1)] bg-black group"
              >
                {/* Node Status Overlay */}
                <div className="absolute top-0 left-0 w-full p-2 flex justify-between items-start z-20 bg-gradient-to-b from-black/80 to-transparent">
                  <div>
                    <span className="px-2 py-1 rounded bg-black/60 border border-brand-cyan/50 text-[10px] text-brand-cyan font-bold uppercase tracking-widest backdrop-blur-md">
                      {node.name}
                    </span>
                    <div className="mt-1 flex items-center gap-1 text-[8px] text-white/50 bg-black/40 px-1.5 py-0.5 w-fit rounded">
                      <div className="w-1.5 h-1.5 rounded-full bg-brand-cyan animate-pulse" />
                      SECURE TUNNEL OP-1
                    </div>
                  </div>
                  <div className="text-[10px] text-brand-cyan/70 font-mono flex items-center gap-1 bg-black/60 px-2 py-1 rounded border border-white/10 backdrop-blur-md">
                    <span className="animate-pulse">REC</span> <span className="w-2 h-2 rounded-full bg-red-500" />
                  </div>
                </div>

                {/* Live Feed Image */}
                <div className="aspect-video relative bg-black/80 flex items-center justify-center overflow-hidden">
                  <div className="absolute inset-0 border-[0.5px] border-brand-cyan/10 m-4 pointer-events-none z-10" />
                  <img 
                    src={`${API_BASE}/api/v2/screen/${node.id}?t=${timestamp}`} 
                    alt={`Live feed from ${node.name}`}
                    className="w-full h-full object-contain filter contrast-125 saturate-50 group-hover:saturate-100 transition-all duration-700"
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.display = 'none';
                      e.currentTarget.parentElement?.classList.add('feed-error');
                    }}
                    onLoad={(e) => {
                      (e.target as HTMLImageElement).style.display = 'block';
                      e.currentTarget.parentElement?.classList.remove('feed-error');
                    }}
                  />
                  {/* Fallback pattern for missing feed */}
                  <div className="absolute inset-0 flex flex-col items-center justify-center text-brand-pink/50 opacity-0 group-[.feed-error]:opacity-100 transition-opacity z-0">
                    <ShieldAlert size={32} className="mb-2 opacity-50" />
                    <span className="text-[10px] uppercase tracking-[0.3em]">Signal Lost</span>
                    <span className="text-[8px] opacity-40 mt-1">Awaiting Telemetry Packet</span>
                  </div>
                </div>

                {/* Footer overlay */}
                <div className="absolute bottom-0 left-0 w-full p-2 flex justify-between items-end z-20 bg-gradient-to-t from-black/80 to-transparent">
                  <div className="text-[8px] text-brand-cyan/50 max-w-[60%] truncate">
                    CMD: /playwright/headless/stream
                  </div>
                  <div className="text-[8px] text-brand-cyan/50 text-right">
                    SYS: ONLINE<br/>
                    PING: {(Math.random() * 20 + 15).toFixed(1)}ms
                  </div>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </motion.div>
      )}
    </main>
  );
}
