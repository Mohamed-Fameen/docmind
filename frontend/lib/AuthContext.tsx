"use client";
// "use client" tells Next.js this component needs to run in the browser (not just be
// rendered once on the server) — required here because we use React state and localStorage,
// both of which only exist in a browser, not during server-side rendering.

import { createContext, useContext, useEffect, useState, ReactNode } from "react";

/**
 * React Context solves a specific problem: without it, sharing the auth token between,
 * say, the login form and the chat window would mean passing it down as a "prop" through
 * every component in between ("prop drilling"), even ones that don't care about it. This
 * lets any component ask "am I logged in, and what's my token?" directly.
 *
 * Token storage: localStorage, for simplicity. Real tradeoff worth knowing: localStorage
 * is readable by any JavaScript running on the page, so it's vulnerable to XSS attacks in
 * a way an httpOnly cookie wouldn't be. Acceptable for a portfolio/dev project; a
 * production app handling real user data would want httpOnly cookies + a CSRF strategy
 * instead.
 */

interface AuthContextValue {
  token: string | null;
  login: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

const STORAGE_KEY = "docmind_token";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);

  // Runs once, after the component mounts in the browser — reads any previously saved
  // token so a page refresh doesn't log the user out.
  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) setToken(saved);
  }, []);

  function login(newToken: string) {
    localStorage.setItem(STORAGE_KEY, newToken);
    setToken(newToken);
  }

  function logout() {
    localStorage.removeItem(STORAGE_KEY);
    setToken(null);
  }

  return (
    <AuthContext.Provider value={{ token, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
