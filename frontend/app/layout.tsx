import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "@/lib/query";
import { AuthProvider } from "@/lib/auth";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "NETRA",
  description: "Unified CCTV viewing and vehicle intelligence for Gujarat Police",
  openGraph: {
    title: "NETRA",
    description: "Unified CCTV viewing and vehicle intelligence for Gujarat Police",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "NETRA" }],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <QueryProvider>
          <AuthProvider>
            {children}
            <Toaster />
          </AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
