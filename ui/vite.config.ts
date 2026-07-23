import path from "node:path"
import { fileURLToPath, URL } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: path.resolve(
      fileURLToPath(new URL(".", import.meta.url)),
      "../purpory/supervise/serve/static",
    ),
    emptyOutDir: true,
    sourcemap: false,
  },
})
