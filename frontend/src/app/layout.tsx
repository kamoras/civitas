import type { Metadata } from "next";
import { VT323, Press_Start_2P, Share_Tech_Mono } from "next/font/google";
import ConfigProvider from "@/components/providers/ConfigProvider";
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
  title: "CIVITAS // PUBLIC RECORD",
  description:
    "See how your senators and representatives vote, score their funding independence, and find civic actions — all from public federal data.",
  keywords: [
    "congressional voting records",
    "campaign finance transparency",
    "political accountability",
    "civic data",
    "Senate scorecard",
    "House scorecard",
  ],
  openGraph: {
    title: "CIVITAS // PUBLIC RECORD",
    description: "Congressional scorecards, campaign finance data, and civic actions — all sourced from public federal records.",
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
      </body>
    </html>
  );
}
