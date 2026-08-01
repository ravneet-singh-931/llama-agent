import { create } from "zustand";
import { api } from "@/lib/api";
import { generateId } from "@/lib/utils";
import type { ChatMessage, Conversation } from "@/types";

interface ChatState {
  messages: ChatMessage[];
  conversations: Conversation[];
  currentConversationId: string | null;
  isStreaming: boolean;
  streamingContent: string;
  error: string | null;

  // Actions
  sendMessage: (content: string) => Promise<void>;
  cancelStream: () => void;
  clearMessages: () => void;
  setConversations: (convs: Conversation[]) => void;
  fetchConversations: () => Promise<void>;
  loadConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  newConversation: () => Promise<void>;
  appendMessage: (msg: ChatMessage) => void;

  // Internal
  _abortController: AbortController | null;
  _setStreaming: (streaming: boolean) => void;
}

function toChatMessage(role: "user" | "assistant", content: string, timestamp: string): ChatMessage {
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
    }));

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
        set({ streamingContent: fullContent });
        // Update last message content
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
          messages: s.messages.map((m) =>
            m.id === assistantId
              ? { ...m, content: fullContent, timestamp: new Date().toISOString() }
              : m
          ),
        }));
      },
      (err) => {
        set((s) => ({
          isStreaming: false,
          streamingContent: "",
          error: err.message,
          messages: s.messages.map((m) =>
            m.id === assistantId
              ? { ...m, content: `Error: ${err.message}` }
              : m
          ),
        }));
      },
      state.currentConversationId
    );

    set({ _abortController: controller });
  },

  cancelStream: () => {
    const state = get();
    if (state._abortController) {
      state._abortController.abort();
      set({ isStreaming: false, _abortController: null });
    }
  },

  clearMessages: () =>
    set({ messages: [], streamingContent: "", error: null }),

  setConversations: (conversations) => set({ conversations }),

  fetchConversations: async () => {
    try {
      const res = await api.getConversations();
      set({ conversations: res.conversations });
      // If no conversation is selected, auto-load the most recent one so the
      // chat window isn't empty after a reload (conversations persist per user).
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
      set({ currentConversationId: id, messages, error: null });
      // Refresh the list so ordering/titles stay current
      get().fetchConversations();
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
      });
      get().fetchConversations();
    } catch (err) {
      set({ error: (err as Error).message, messages: [], currentConversationId: null });
    }
  },
}));
