import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        danger: "hsl(var(--danger))",
        warn: "hsl(var(--warn))",
        ok: "hsl(var(--ok))",
      },
      borderRadius: { lg: "0.75rem", md: "0.5rem", sm: "0.375rem" },
      fontFamily: { sans: ["var(--font-sans)", "system-ui", "sans-serif"] },
      keyframes: { shimmer: { "100%": { transform: "translateX(100%)" } } },
    },
  },
  plugins: [],
};
export default config;
