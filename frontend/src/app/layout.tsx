import type { Metadata } from "next";
import { Archivo, VT323, Press_Start_2P, Share_Tech_Mono } from "next/font/google";
import ConfigProvider from "@/components/providers/ConfigProvider";
import "./globals.css";

// Display and prose. 400 for body, 600 for headings, 800 for the blunt
// statement type the masthead is built on — a grotesque at heavy weight
// reads broadsheet and poster, where a bookish serif reads endowment.
const archivo = Archivo({
  weight: ["400", "600", "800"],
  subsets: ["latin"],
  variable: "--font-archivo",
  display: "swap",
});

// DEPRECATED — kept loaded only so the ~150 call sites still naming
// `font-terminal` keep rendering while they migrate off it. See the
// fontFamily note in tailwind.config.ts.
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
      {/*
        `font-mono` (Share Tech Mono), not `font-terminal` (VT323).
        This governs every element that does not name a font itself, which is
        most of the site's prose. Measured from the shipped woff2 files:

                          x-height/em   advance/em
          VT323               0.400        0.400
          Share Tech Mono     0.500        0.540

        Lowercase letters get 25% taller at the same nominal size — at
        `text-sm` (14px, the most common body size) they were rendering 5.6px
        tall. Share Tech Mono also has a slashed zero and distinguishes I/l/1,
        where VT323's O/0 are near-identical ovals.

        The cost, which is not free: the advance width is 35% wider, so lines
        hold ~26% fewer characters and every fixed-width container that holds
        inherited text gets tighter. Verified against the densest surfaces
        (bill rows, stage flow, senator scorecard) before landing; see the two
        width bumps in BillRow and BillStageFlow that this required.
      */}
      <body
        className={`${archivo.variable} ${vt323.variable} ${pressStart.variable} ${shareTech.variable} font-mono antialiased`}
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
