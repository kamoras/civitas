import type { Metadata } from "next";
import { VT323, Press_Start_2P, Share_Tech_Mono } from "next/font/google";
import ConfigProvider from "@/components/providers/ConfigProvider";
import { PlainLanguageProvider } from "@/context/PlainLanguageContext";
import "./globals.css";

const vt323 = VT323({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-vt323",
});

const pressStart = Press_Start_2P({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-press-start",
});

const shareTech = Share_Tech_Mono({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-share-tech",
});

export const metadata: Metadata = {
  title: "CIVITAS // EXPOSE THE MACHINE",
  description:
    "Track corporate money in politics, see how your senators vote, and follow the receipts. All public data, zero spin.",
  keywords: [
    "political corruption",
    "senator corruption",
    "corporate lobbying",
    "campaign finance",
    "money in politics",
  ],
  openGraph: {
    title: "CIVITAS // EXPOSE THE MACHINE",
    description: "Track corporate money in politics. See how your senators vote. Follow the receipts.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${vt323.variable} ${pressStart.variable} ${shareTech.variable} font-terminal antialiased`}
      >
        <PlainLanguageProvider>
          <ConfigProvider>
            <a
              href="#main-content"
              className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:top-2 focus-visible:left-2 focus-visible:z-[10000] focus-visible:bg-crt-black focus-visible:text-matrix-green focus-visible:border-2 focus-visible:border-matrix-green focus-visible:px-4 focus-visible:py-2 focus-visible:text-lg focus-visible:font-terminal"
            >
              Skip to main content
            </a>
            <div className="crt-overlay" aria-hidden="true" />
            {children}
          </ConfigProvider>
        </PlainLanguageProvider>
      </body>
    </html>
  );
}
