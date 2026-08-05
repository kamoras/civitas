import type { Metadata } from "next";
import { VT323, Press_Start_2P, Share_Tech_Mono, JetBrains_Mono } from "next/font/google";
import ConfigProvider from "@/components/providers/ConfigProvider";
import { DISPLAY_SETTINGS_KEY } from "@/hooks/useDisplaySettings";
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

/**
 * Only referenced by the `data-legible="on"` rules in globals.css, so
 * `preload: false` keeps it out of the critical path — readers who never turn
 * the setting on never fetch it. Chosen over the three existing faces for the
 * highest x-height of any candidate measured (0.550em vs VT323's 0.400em) and
 * for disambiguating 0/O and 1/l/I, which matters on pages that are mostly
 * bill numbers, vote tallies and dates.
 */
const jetbrains = JetBrains_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-jetbrains",
  preload: false,
});

/**
 * Applies saved display settings before first paint. Without this the page
 * paints the default terminal palette and then swaps, which is both a flash
 * and — for someone who turned the terminal palette off because it is hard
 * on their eyes — a flash of exactly the thing they opted out of.
 *
 * Kept in sync with applySettings() in useDisplaySettings.ts.
 */
const BOOTSTRAP = `(function(){try{
var s=JSON.parse(localStorage.getItem(${JSON.stringify(DISPLAY_SETTINGS_KEY)})||"{}");
var e=document.documentElement;
e.dataset.theme=s.theme==="light"?"light":"dark";
e.dataset.legible=s.legible===true?"on":"off";
e.dataset.effects=s.effects===false?"off":"on";
var v=[100,112,125,150].indexOf(s.textScale)>-1?s.textScale:100;
e.style.setProperty("--civitas-text-scale",String(v/100));
}catch(_){}})();`;

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
    description:
      "Congressional scorecards, campaign finance data, and civic actions — all sourced from public federal records.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-theme="dark" data-legible="off" data-effects="on">
      <head>
        <script dangerouslySetInnerHTML={{ __html: BOOTSTRAP }} />
      </head>
      {/*
        `font-mono` (Share Tech Mono), not `font-terminal` (VT323).
        This one word governs every element on the site that does not name a
        font itself — including 114 of the 167 <p> elements, i.e. most of the
        actual prose. VT323 has a 0.400em x-height, the smallest of any face
        available here, so at the site's most common body size (text-sm, 14px)
        its lowercase letters rendered 5.6px tall; Share Tech Mono's 0.500em
        renders the same nominal size at 7.0px, a 25% gain in apparent size
        for no layout change and no extra font download. VT323 remains
        available as `font-terminal` for deliberate display use.

        This also makes the accessibility statement true: it already claimed
        "all body text and data use Share Tech Mono or system monospace
        fonts", which was not the case while the body element said otherwise.
      */}
      <body
        className={`${vt323.variable} ${pressStart.variable} ${shareTech.variable} ${jetbrains.variable} font-mono antialiased`}
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
