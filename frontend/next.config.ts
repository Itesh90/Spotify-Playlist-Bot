import type { NextConfig } from "next";

const backendUrl =
  process.env.BACKEND_URL ||
  (process.env.NODE_ENV === "production"
    ? "http://backend:5000"   // Docker: "backend" = docker-compose service name
    : "http://localhost:5000"); // Local dev

const nextConfig: NextConfig = {
  output: "standalone",
  reactCompiler: true,

  // Proxy all /api/* and OAuth routes to the Flask backend.
  // This eliminates CORS — browser only talks to port 3000 (same-origin).
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      { source: "/authorize/:path*", destination: `${backendUrl}/authorize/:path*` },
      { source: "/callback", destination: `${backendUrl}/callback` },
    ];
  },
};

export default nextConfig;
