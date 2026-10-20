import type { NextConfig } from "next";
import path from "node:path";
const config: NextConfig = {
  ...(process.env.NEXT_PUBLIC_DEPLOYMENT === "pages"
    ? {
        output: "export" as const,
        trailingSlash: true,
        basePath: process.env.NEXT_PUBLIC_BASE_PATH ?? "",
      }
    : {}),
  turbopack: { root: path.resolve(process.cwd()) },
  devIndicators: false,
  allowedDevOrigins: ["127.0.0.1"],
};
export default config;
