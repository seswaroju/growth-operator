import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The operator app runs on its own port (5174) — separate from the customer app (5173).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5174 },
})
