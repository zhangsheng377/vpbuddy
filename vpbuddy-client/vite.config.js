import { defineConfig } from "vite";

// Vite config — Tauri 期望固定端口 1420 + HMR 1421
export default defineConfig({
  root: "ui",
  publicDir: "../public",
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    hmr: {
      port: 1421,
      protocol: "ws",
    },
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    target: "es2021",
    outDir: "../dist",
    emptyOutDir: true,
    rollupOptions: {
      input: "ui/index.html",
    },
  },
});
