"use client";

import Link from "next/link";
import { LogIn } from "lucide-react";
import { useState } from "react";
import { useRouter } from "next/navigation";
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
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen relative z-10 flex flex-col items-center justify-center p-6">
      <div className="glass-panel w-full max-w-md p-10 rounded-[2rem] text-center relative overflow-hidden">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-32 h-32 bg-brand-pink/20 blur-3xl rounded-full"></div>

        <h1 className="text-3xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-brand-cyan via-brand-blue to-brand-pink mb-2 relative z-10">
          SpotifyBot
        </h1>
        <p className="text-white/60 text-sm mb-8 relative z-10">
          Access your automated playlist dashboard
        </p>

        {error && (
          <div className="mb-4 p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm text-left relative z-10">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5 text-left relative z-10">
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-white/50 uppercase tracking-widest pl-1">
              Username
            </label>
            <input
              type="text"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-5 py-3.5 bg-black/20 border border-white/10 rounded-xl focus:outline-none focus:border-brand-purple focus:ring-1 focus:ring-brand-purple transition-all text-sm text-white placeholder:text-white/30"
              placeholder="Enter your username"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-white/50 uppercase tracking-widest pl-1">
              Password
            </label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-5 py-3.5 bg-black/20 border border-white/10 rounded-xl focus:outline-none focus:border-brand-purple focus:ring-1 focus:ring-brand-purple transition-all text-sm text-white placeholder:text-white/30"
              placeholder="Enter your password"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full mt-2 flex justify-center items-center gap-2 px-5 py-3.5 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-brand-purple to-brand-pink shadow-[0_4px_20px_rgba(255,0,127,0.3)] hover:-translate-y-0.5 hover:shadow-[0_8px_25px_rgba(255,0,127,0.5)] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <LogIn size={18} /> {loading ? "Logging in..." : "Login"}
          </button>
        </form>
      </div>
    </main>
  );
}
