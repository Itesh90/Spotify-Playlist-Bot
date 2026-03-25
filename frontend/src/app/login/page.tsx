"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LogIn, ShieldCheck, Activity, KeyRound, User } from "lucide-react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";

export default function Login() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api("/api/login", { method: "POST", body: { username, password } });
      router.push("/");
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen relative z-10 flex flex-col items-center justify-center p-6 overflow-hidden">
      {/* Background ambient glows */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-brand-cyan/20 rounded-full blur-[100px] -z-10 animate-pulse" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-brand-purple/20 rounded-full blur-[100px] -z-10 animate-pulse" style={{ animationDelay: '2s' }} />

      <motion.div 
        initial={{ opacity: 0, y: 30, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
        className="glass-panel w-full max-w-md p-10 rounded-[2rem] relative overflow-hidden border-2 border-white/10 shadow-[0_0_50px_rgba(0,0,0,0.5)]"
      >
        <div className="absolute top-0 right-0 w-32 h-32 bg-brand-pink/10 blur-3xl rounded-full" />
        
        <div className="flex flex-col items-center text-center relative z-10 mb-8">
          <motion.div 
            initial={{ scale: 0, rotate: -180 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ type: "spring", stiffness: 200, damping: 20, delay: 0.2 }}
            className="w-16 h-16 rounded-2xl bg-gradient-to-br from-brand-cyan to-brand-blue flex items-center justify-center shadow-[0_0_20px_rgba(0,242,254,0.5)] mb-4"
          >
            <ShieldCheck className="text-white" size={32} />
          </motion.div>
          <motion.h1 
            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}
            className="text-3xl font-bold text-white tracking-tight mb-1"
          >
            Fleet <span className="text-gradient">Sentinel</span>
          </motion.h1>
          <motion.p 
            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }}
            className="text-brand-cyan/70 text-xs font-mono tracking-widest uppercase flex items-center gap-2"
          >
            <Activity size={12} /> Secure Access Portal
          </motion.p>
        </div>

        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }}>
          {error && (
            <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} className="mb-6 p-4 rounded-xl bg-brand-pink/10 border border-brand-pink/30 text-brand-pink text-sm text-center relative z-10 font-mono shadow-[0_0_15px_rgba(255,0,127,0.2)]">
              {error}
            </motion.div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5 text-left relative z-10">
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-brand-cyan/70 uppercase tracking-widest pl-1">
                Operator ID
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                  <User size={16} className="text-white/40" />
                </div>
                <input
                  type="text"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="w-full pl-11 pr-5 py-3.5 bg-black/40 border border-white/10 rounded-xl focus:outline-none focus:border-brand-cyan transition-all text-sm text-white placeholder:text-white/20 shadow-inner"
                  placeholder="admin"
                />
              </div>
            </div>
            
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold text-brand-cyan/70 uppercase tracking-widest pl-1">
                Passcode
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                  <KeyRound size={16} className="text-white/40" />
                </div>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full pl-11 pr-5 py-3.5 bg-black/40 border border-white/10 rounded-xl focus:outline-none focus:border-brand-purple transition-all text-sm text-white placeholder:text-white/20 shadow-inner"
                  placeholder="••••••••"
                />
              </div>
            </div>

            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              type="submit"
              disabled={loading}
              className="w-full mt-4 flex justify-center items-center gap-2 px-5 py-4 rounded-xl text-sm font-bold text-black bg-gradient-to-r from-brand-cyan to-brand-blue shadow-[0_0_20px_rgba(0,242,254,0.4)] transition-all disabled:opacity-50 disabled:cursor-not-allowed uppercase tracking-wider"
            >
              {loading ? <Activity className="animate-spin" size={18} /> : <LogIn size={18} />}
              {loading ? "Authenticating..." : "Initialize Session"}
            </motion.button>
          </form>
        </motion.div>
      </motion.div>
      
      <motion.div 
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1 }}
        className="mt-8 text-white/30 text-[10px] font-mono uppercase tracking-widest text-center"
      >
        Spotify Automation System V2.0<br/>
        End-to-End Encrypted Tunnel
      </motion.div>
    </main>
  );
}

