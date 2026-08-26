"use client";

import ReactMarkdown from "react-markdown";
import { SourceRef } from "@/lib/api";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: SourceRef[];
  classification?: string;
  retries?: number;
}

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-2xl rounded-xl px-4 py-3 text-sm ${
          isUser ? "bg-blue-600 text-white" : "bg-white text-slate-800 shadow-sm"
        }`}
      >
        {isUser ? (
          <p>{message.content}</p>
        ) : (
          <>
            {/* react-markdown renders the answer's markdown (code blocks, bold text from
                citations like [1]) as actual formatted HTML instead of raw asterisks/backticks */}
            <div className="prose prose-sm max-w-none">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>

            {message.sources && message.sources.length > 0 && (
              <div className="mt-3 border-t border-slate-100 pt-2">
                <p className="mb-1 text-xs font-medium text-slate-400">SOURCES</p>
                <ul className="space-y-1">
                  {message.sources.map((source) => (
                    <li key={source.number} className="text-xs">
                      <span
                        className={`mr-1 rounded px-1 font-mono ${
                          source.cited
                            ? "bg-blue-100 text-blue-700"
                            : "bg-slate-100 text-slate-400"
                        }`}
                        title={source.cited ? "Cited in the answer" : "Retrieved but not cited"}
                      >
                        [{source.number}]
                      </span>
                      <a
                        href={source.doc_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-blue-600 hover:underline"
                        title={source.text_snippet}
                      >
                        {source.heading_path}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {(message.classification || message.retries) && (
              <p className="mt-2 text-xs text-slate-300">
                {message.classification}
                {message.retries ? ` · ${message.retries} retry` : ""}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
