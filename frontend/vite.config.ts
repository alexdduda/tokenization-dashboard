import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so the built bundle works from any path — a Vercel root deploy, a
// GitHub Pages subpath, or opened straight off disk.
export default defineConfig({
  base: "./",
  plugins: [react()],
});
