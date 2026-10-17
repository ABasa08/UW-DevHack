import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#16181d", soft: "#3b3f48", mute: "#6b7079", faint: "#9aa0a9" },
        paper: { DEFAULT: "#f7f6f2", card: "#ffffff", line: "#e6e3dc", sunk: "#efede7" },
        brand: { DEFAULT: "#1f3a8a", soft: "#e8edfb", ink: "#132557" },
        pass: { DEFAULT: "#1f7a4d", soft: "#e6f4ec" },
        fail: { DEFAULT: "#b4382f", soft: "#fbeceb" },
        warn: { DEFAULT: "#a8650b", soft: "#fdf3e1" },
        info: { DEFAULT: "#355d8c", soft: "#eaf1f9" },
        ember: "#e07a2f",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "ui-serif", "Georgia", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(22,24,29,0.04), 0 4px 16px -8px rgba(22,24,29,0.08)",
        lift: "0 2px 4px rgba(22,24,29,0.06), 0 12px 32px -12px rgba(22,24,29,0.18)",
      },
    },
  },
  plugins: [],
} satisfies Config;
