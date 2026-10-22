import { create } from "zustand";
import { api } from "@/lib/api";
import { generateId } from "@/lib/utils";
import type { ChatMessage, Conversation } from "@/types";
import type { DsaOptions, StreamEvent } from "@/lib/api";

interface ChatState {
  messages: ChatMessage[];
  conversations: Conversation[];
  currentConversationId: string | null;
  isStreaming: boolean;
  streamingContent: string;
  error: string | null;
  // DSA feature toggles surfaced in the control panel.
  dsaOptions: DsaOptions;
  // Structured SSE events (config / reasoning) for the live reasoning view.
  reasoning: StreamEvent[];
  // Seconds the last completed answer took (shown under the reply).
  lastResponseTime: number | null;

  // Actions
  sendMessage: (content: string) => Promise<void>;
  cancelStream: () => void;
  clearMessages: () => void;
  setConversations: (convs: Conversation[]) => void;
  fetchConversations: () => Promise<void>;
  refreshConversations: () => Promise<void>;
  loadConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  newConversation: () => Promise<void>;
  appendMessage: (msg: ChatMessage) => void;
  setDsaOptions: (opts: DsaOptions) => void;

  // Internal
  _abortController: AbortController | null;
  _setStreaming: (streaming: boolean) => void;
}

function toChatMessage(
  role: "user" | "assistant",
  content: string,
  timestamp: string
): ChatMessage {
  return {
    id: generateId(),
    role,
    content,
    timestamp: timestamp || new Date().toISOString(),
  };
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  conversations: [],
  currentConversationId: null,
  isStreaming: false,
  streamingContent: "",
  error: null,
  dsaOptions: {},
  reasoning: [],
  lastResponseTime: null,
  _abortController: null,

  _setStreaming: (streaming) => set({ isStreaming: streaming }),

  appendMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg] })),

  sendMessage: async (content) => {
    const state = get();
    const userMsg: ChatMessage = {
      id: generateId(),
      role: "user",
      content,
      timestamp: new Date().toISOString(),
    };
    set((s) => ({
      messages: [...s.messages, userMsg],
      isStreaming: true,
      streamingContent: "",
      error: null,
      reasoning: [],
      lastResponseTime: null,
    }));
    const startedAt = Date.now();

    // Add assistant placeholder
    const assistantId = generateId();
    set((s) => ({
      messages: [
        ...s.messages,
        {
          id: assistantId,
          role: "assistant",
          content: "",
          timestamp: new Date().toISOString(),
        },
      ],
    }));

    let fullContent = "";

    const controller = api.chatStream(
      content,
      (token) => {
        fullContent += token;
        // Update ONLY the streaming message (unchanged messages keep their
        // object identity, so React.memo skips re-rendering them).
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantId ? { ...m, content: fullContent } : m
          ),
        }));
      },
      () => {
        set((s) => ({
          isStreaming: false,
          streamingContent: "",
          _abortController: null,
          lastResponseTime: (Date.now() - startedAt) / 1000,
          messages: s.messages.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: fullContent.trim() || "No response received.",
                  timestamp: new Date().toISOString(),
                }
              : m
          ),
        }));
        // New/renamed conversation + last-message preview in the sidebar.
        void get().refreshConversations();
      },
      (err) => {
        set((s) => ({
          isStreaming: false,
          streamingContent: "",
          _abortController: null,
          error: err.message,
          messages: s.messages.map((m) =>
            m.id === assistantId
              ? { ...m, content: `Error: ${err.message}` }
              : m
          ),
        }));
      },
      state.currentConversationId,
      state.dsaOptions,
      (event) => {
        // Collect config / reasoning / agent events for the reasoning view.
        set((s) => ({ reasoning: [...s.reasoning, event] }));
        if (event.type === "agent_start" && typeof event.agent === "string") {
          const agent = event.agent;
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === assistantId ? { ...m, agent } : m
            ),
          }));
        }
      }
    );

    set({ _abortController: controller });
  },

  cancelStream: () => {
    const state = get();
    if (state._abortController) {
      state._abortController.abort();
      set({ isStreaming: false, _abortController: null });
      void get().refreshConversations();
    }
  },

  clearMessages: () =>
    set({ messages: [], streamingContent: "", error: null, reasoning: [] }),

  setConversations: (conversations) => set({ conversations }),

  setDsaOptions: (dsaOptions) => set({ dsaOptions }),

  refreshConversations: async () => {
    try {
      const res = await api.getConversations();
      set({ conversations: res.conversations });
    } catch {
      // Sidebar refresh is best-effort.
    }
  },

  fetchConversations: async () => {
    try {
      const res = await api.getConversations();
      set({ conversations: res.conversations });
      const state = get();
      if (!state.currentConversationId && res.conversations.length > 0) {
        const mostRecent = res.conversations[0];
        await get().loadConversation(mostRecent.id);
      }
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  loadConversation: async (id) => {
    try {
      const conv = await api.getConversation(id);
      const messages = conv.messages.map((m) =>
        toChatMessage(m.role, m.content, m.timestamp)
      );
      set({ currentConversationId: id, messages, error: null, reasoning: [] });
      void get().refreshConversations();
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  deleteConversation: async (id) => {
    try {
      await api.deleteConversation(id);
      set((s) => ({
        conversations: s.conversations.filter((c) => c.id !== id),
        currentConversationId:
          s.currentConversationId === id ? null : s.currentConversationId,
        messages: s.currentConversationId === id ? [] : s.messages,
      }));
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  newConversation: async () => {
    try {
      const conv = await api.createConversation();
      set({
        messages: [],
        currentConversationId: conv.id,
        streamingContent: "",
        error: null,
        reasoning: [],
      });
      void get().refreshConversations();
    } catch (err) {
      set({ error: (err as Error).message, messages: [], currentConversationId: null });
    }
  },
}));
