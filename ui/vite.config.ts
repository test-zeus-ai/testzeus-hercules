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
import { buildCss } from "@yusufceng1z/ding";
import { defineConfig, type Plugin } from "vite";

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

// The toast library writes its stylesheet into a <style> tag at runtime, which the daemon's CSP (style-src 'self')
// refuses. Its CSS is served as a module instead, bundled into the hashed stylesheet, and ding-react gets the
// library through src/lib/notify/dingStyles.ts, which skips the injection.
function dingCss(): Plugin {
  const id = "virtual:ding.css";
  const resolved = `\0${id}`;
  const withoutInjection = fileURLToPath(new URL("./src/lib/notify/dingStyles.ts", import.meta.url));
  return {
    name: "hercules-ding-css",
    enforce: "pre",
    resolveId(source, importer) {
      if (source === id) return resolved;
      if (source === "@yusufceng1z/ding" && importer?.includes("/@yusufceng1z/ding-react/")) return withoutInjection;
      return undefined;
    },
    load: (source) => (source === resolved ? buildCss() : undefined),
  };
}

const daemon = readDaemon();
const target = process.env.HERCULES_DAEMON_URL || `http://127.0.0.1:${daemon?.port ?? 7373}`;

export default defineConfig({
  plugins: [react(), dingCss()],
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
