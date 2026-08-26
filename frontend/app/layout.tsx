import type { Metadata } from "next";
import { AuthProvider } from "@/lib/AuthContext";
import "./globals.css";

export const metadata: Metadata = {
  title: "DocMind",
  description: "Kubernetes documentation assistant",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* Everything inside AuthProvider can call useAuth() to read the token or log
            in/out, without passing it down manually through every component in between. */}
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
