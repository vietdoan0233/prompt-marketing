/** @type {import('next').NextConfig} */
const apiUrl = process.env.API_URL || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  // Browser calls go to /api/* and are proxied to FastAPI, so no CORS or public API URL is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }];
  },
};

export default nextConfig;
