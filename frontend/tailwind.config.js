/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        surface: {
          DEFAULT: "hsl(var(--surface))",
          muted: "hsl(var(--surface-muted))"
        },
        border: "hsl(var(--border))",
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted))"
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))"
        },
        danger: {
          DEFAULT: "hsl(var(--danger))",
          foreground: "hsl(var(--danger-foreground))"
        },
        warning: "hsl(var(--warning))",
        sidebar: {
          DEFAULT: "hsl(var(--sidebar))",
          foreground: "hsl(var(--sidebar-foreground))",
          active: "hsl(var(--sidebar-active))",
          border: "hsl(var(--sidebar-border))"
        },
        state: {
          running: "hsl(var(--state-running))",
          completed: "hsl(var(--state-completed))",
          failed: "hsl(var(--state-failed))",
          cancelled: "hsl(var(--state-cancelled))",
          pending: "hsl(var(--state-pending))",
          scheduled: "hsl(var(--state-scheduled))",
          paused: "hsl(var(--state-paused))",
          crashed: "hsl(var(--state-crashed))",
          claimed: "hsl(var(--state-claimed))",
          online: "hsl(var(--state-online))"
        }
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)"
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"]
      }
    }
  },
  plugins: []
};
