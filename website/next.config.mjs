/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  basePath: "/ASSERT",
  assetPrefix: "/ASSERT",
  trailingSlash: true,
  images: {
    unoptimized: true,
    remotePatterns: [
      {
        protocol: "https",
        hostname: "www.figma.com"
      }
    ]
  },
  webpack: (config, { dev }) => {
    if (dev) {
      config.cache = false;
    }
    return config;
  }
};

export default nextConfig;
