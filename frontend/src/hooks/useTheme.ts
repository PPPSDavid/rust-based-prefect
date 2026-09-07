import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "ironflow-theme";

export type ThemeMode = "light" | "dark" | "system";

function resolveTheme(mode: ThemeMode): "light" | "dark" {
  if (mode === "light" || mode === "dark") return mode;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(mode: ThemeMode) {
  const resolved = resolveTheme(mode);
  document.documentElement.classList.toggle("dark", resolved === "dark");
  document.documentElement.dataset.theme = resolved;
}

export function getStoredTheme(): ThemeMode {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "system";
}

/** Apply theme early (call from main.tsx before render). */
export function initTheme() {
  applyTheme(getStoredTheme());
}

export function useTheme() {
  const [mode, setModeState] = useState<ThemeMode>(() =>
    typeof window === "undefined" ? "system" : getStoredTheme()
  );

  useEffect(() => {
    applyTheme(mode);
    localStorage.setItem(STORAGE_KEY, mode);
  }, [mode]);

  useEffect(() => {
    if (mode !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => setModeState(next), []);

  const cycle = useCallback(() => {
    // One click always flips the visible theme (system resolves first).
    setModeState((prev) => {
      const current = prev === "system" ? resolveTheme("system") : prev;
      return current === "dark" ? "light" : "dark";
    });
  }, []);

  return {
    mode,
    setMode,
    cycle,
    resolved: typeof window === "undefined" ? "dark" : resolveTheme(mode === "system" ? "system" : mode)
  };
}
