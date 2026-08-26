"use client";

import { useAuth } from "@/lib/AuthContext";
import LoginForm from "@/components/LoginForm";
import ChatWindow from "@/components/ChatWindow";

export default function Home() {
  const auth = useAuth();

  // The core routing logic for this whole app: no token -> show the login form;
  // token present -> show the chat window. No separate /login URL needed for an app
  // this small — one page, one conditional.
  return auth.token ? <ChatWindow /> : <LoginForm />;
}
