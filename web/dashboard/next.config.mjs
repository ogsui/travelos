/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Playwright drives the dev server over 127.0.0.1; allow its HMR/dev requests.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
