"use client";

import { useState, useRef, useEffect } from "react";
import { sendQuery, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/AuthContext";
import MessageBubble, { ChatMessage } from "@/components/MessageBubble";
import ModelSelector from "@/components/ModelSelector";

export default function ChatWindow() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const auth = useAuth();

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    const text = input.trim();
    if (!text || !auth.token) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setError(null);
    setLoading(true);

    try {
      const result = await sendQuery(auth.token, text, conversationId, selectedModel);
      setConversationId(result.conversation_id);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.answer,
          sources: result.sources,
          classification: result.classification,
          retries: result.retries,
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        auth.logout(); // token expired or invalid — send back to login
        return;
      }
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-screen flex-col bg-slate-50">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <h1 className="text-lg font-semibold text-slate-900">DocMind</h1>
        <div className="flex items-center gap-3">
          <ModelSelector selected={selectedModel} onChange={setSelectedModel} />
          <button
            onClick={auth.logout}
            className="text-sm text-slate-500 hover:text-slate-700"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-6">
        {messages.length === 0 && (
          <p className="text-center text-sm text-slate-400">
            Ask anything about Kubernetes to get started.
          </p>
        )}
        {messages.map((message, i) => (
          <MessageBubble key={i} message={message} />
        ))}

        {/* Explicit "thinking" state instead of streamed tokens — see docs/07-frontend.md
            for why real token streaming was deliberately deferred: the agent graph runs
            several sequential LLM calls (classify, maybe rewrite, generate, confidence
            check) before a final answer exists, so there's no single token stream to show
            progress from until the very last step. On CPU-bound local generation this wait
            can genuinely be 30-90+ seconds, so it's important this doesn't look frozen. */}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-xl bg-white px-4 py-3 text-sm text-slate-400 shadow-sm">
              Thinking...
            </div>
          </div>
        )}

        {error && <p className="text-center text-sm text-red-600">{error}</p>}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-slate-200 bg-white px-6 py-4">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !loading && handleSend()}
            placeholder="Ask a question about Kubernetes..."
            className="flex-1 rounded-lg border border-slate-300 px-4 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
