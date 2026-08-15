import { create } from "zustand";
import { api } from "@/lib/api";
import type { User } from "@/types";

interface AuthState {
  user: User | null;
  isLoggedIn: boolean;
  isLoading: boolean;
  error: string | null;

  login: (username: string, password: string) => Promise<boolean>;
  register: (username: string, email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoggedIn: false,
  isLoading: false,
  error: null,

  login: async (username, password) => {
    set({ isLoading: true, error: null });
    try {
      await api.login(username, password);
      // Cookies are set by the backend; fetch the identity via the cookie.
      const me = await api.getMe();
      set({
        user: me.user,
        isLoggedIn: true,
        isLoading: false,
      });
      return true;
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Login failed";
      set({ error: message, isLoading: false });
      return false;
    }
  },

  register: async (username, email, password) => {
    set({ isLoading: true, error: null });
    try {
      await api.register(username, email, password);
      const me = await api.getMe();
      set({
        user: me.user,
        isLoggedIn: true,
        isLoading: false,
      });
      return true;
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Registration failed";
      set({ error: message, isLoading: false });
      return false;
    }
  },

  logout: async () => {
    await api.logout();
    set({ user: null, isLoggedIn: false });
  },

  checkAuth: async () => {
    // No token in JS - just ask the backend (cookie is sent automatically).
    try {
      const me = await api.getMe();
      set({ user: me.user, isLoggedIn: true });
    } catch {
      set({ user: null, isLoggedIn: false });
    }
  },
}));
