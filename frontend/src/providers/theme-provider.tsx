"use client";

import { createContext, useCallback, useContext, useEffect, useSyncExternalStore, type ReactNode } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

export type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "bankease-theme";
type ThemeContextValue = { theme: Theme; toggleTheme: () => void };
const ThemeContext = createContext<ThemeContextValue | null>(null);
const themeListeners = new Set<() => void>();

function storedTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "dark" || stored === "light" ? stored : null;
  } catch { return null; }
}

function persistTheme(theme: Theme) {
  try { localStorage.setItem(THEME_STORAGE_KEY, theme); } catch { /* Theme switching remains available for this session. */ }
}

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: Theme, animate = false) {
  const root = document.documentElement;
  if (animate) {
    root.classList.add("theme-transition");
    window.setTimeout(() => root.classList.remove("theme-transition"), 220);
  }
  root.dataset.theme = theme;
}

function readTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function subscribeToTheme(listener: () => void) {
  themeListeners.add(listener);
  return () => themeListeners.delete(listener);
}

function publishTheme(theme: Theme, animate = false) {
  applyTheme(theme, animate);
  themeListeners.forEach((listener) => listener());
}

export function ThemeProvider({ children }: Readonly<{ children: ReactNode }>) {
  const theme = useSyncExternalStore<Theme>(subscribeToTheme, readTheme, () => "light");

  useEffect(() => {
    const root = document.documentElement;
    const stored = storedTheme();
    const initial = stored === "dark" || stored === "light"
      ? stored
      : root.dataset.theme === "dark" || root.dataset.theme === "light"
        ? root.dataset.theme
        : systemTheme();

    applyTheme(initial);

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const syncSystemTheme = () => {
      if (storedTheme() === null) {
        const next = systemTheme();
        publishTheme(next, true);
      }
    };
    const syncOtherTabs = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY) return;
      const next: Theme = event.newValue === "dark" ? "dark" : event.newValue === "light" ? "light" : systemTheme();
      publishTheme(next, true);
    };

    media.addEventListener("change", syncSystemTheme);
    window.addEventListener("storage", syncOtherTabs);
    return () => {
      media.removeEventListener("change", syncSystemTheme);
      window.removeEventListener("storage", syncOtherTabs);
    };
  }, []);

  const toggleTheme = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    persistTheme(next);
    publishTheme(next, true);
  }, [theme]);

  return <ThemeContext.Provider value={{ theme, toggleTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === "dark";
  return <Button
    variant="ghost"
    size="icon"
    onClick={toggleTheme}
    aria-label={`Switch to ${isDark ? "light" : "dark"} mode`}
    aria-pressed={isDark}
    title={`${isDark ? "Dark" : "Light"} mode`}
    className="shrink-0 text-slate-600"
  >
    {isDark ? <Moon size={17} aria-hidden="true" /> : <Sun size={17} aria-hidden="true" />}
  </Button>;
}
