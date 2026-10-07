/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Static export for single-container deploys (API serves ./out).
  output: process.env.NEXT_STATIC_EXPORT === "1" ? "export" : undefined,
  // In server mode, proxy /api/* to the FastAPI backend on the same host.
  // Lets one public port serve both web and API (Hugging Face Spaces).
  async rewrites() {
    if (process.env.NEXT_STATIC_EXPORT === "1") return [];
    const apiOrigin =
      process.env.ARGUS_API_ORIGIN || "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
};

module.exports = nextConfig;
