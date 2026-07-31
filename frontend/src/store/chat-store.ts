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
  loadConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  newConversation: () => void;
  appendMessage: (msg: ChatMessage) => void;

  // Internal
  _abortController: AbortController | null;
  _setStreaming: (streaming: boolean) => void;
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
      }
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

  loadConversation: (id) => {
    // In a full implementation, this would fetch messages from API
    set({ currentConversationId: id });
  },

  deleteConversation: (id) => {
    set((s) => ({
      conversations: s.conversations.filter((c) => c.id !== id),
      currentConversationId:
        s.currentConversationId === id ? null : s.currentConversationId,
    }));
  },

  newConversation: () =>
    set({ messages: [], currentConversationId: null }),
}));
