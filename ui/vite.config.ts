/// <reference types="vitest/config" />
// Vite config for the Hercules tester UI.
//
// `npm run build` writes the bundle into the Python package (testzeus_hercules/ui/static),
// which the daemon serves at http://127.0.0.1:7373/. `npm run dev` serves the UI on
// http://localhost:5173 and proxies /api to the local daemon; the proxy adds the daemon's
// API token from ~/.hercules/daemon.json on the Node side, so the browser never sees it.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

interface DaemonInfo {
  host: string;
  port: number;
  token: string;
}

function daemonFile(): string {
  const home = process.env.HERCULES_HOME || join(homedir(), ".hercules");
  return join(home, "daemon.json");
}

function readDaemon(): DaemonInfo | null {
  try {
    const info = JSON.parse(readFileSync(daemonFile(), "utf-8")) as DaemonInfo;
    return typeof info.port === "number" && typeof info.token === "string" ? info : null;
  } catch {
    return null;
  }
}

const daemon = readDaemon();
const target = process.env.HERCULES_DAEMON_URL || `http://127.0.0.1:${daemon?.port ?? 7373}`;

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: fileURLToPath(new URL("../testzeus_hercules/ui/static", import.meta.url)),
    emptyOutDir: true,
    assetsDir: "assets",
    // No data: URIs (the daemon's CSP allows same-origin files only) and no inline preload script.
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    sourcemap: false,
    target: "es2022",
  },
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        ws: true, // the live view's screencast WebSocket
        configure: (proxy) => {
          proxy.on("proxyReq", (request) => {
            const current = readDaemon();
            if (current) request.setHeader("authorization", `Bearer ${current.token}`);
          });
          proxy.on("proxyReqWs", (request) => {
            const current = readDaemon();
            if (current) request.setHeader("authorization", `Bearer ${current.token}`);
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
