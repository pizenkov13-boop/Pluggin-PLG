import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: "./" → prod build works with localhost static server in plg_webview.py
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: Number(process.env.PORT) || 5173,
    fs: { allow: [".."] },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
