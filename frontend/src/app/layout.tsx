import type { Metadata } from "next";
import { Archivo, Press_Start_2P, Share_Tech_Mono } from "next/font/google";
import ConfigProvider from "@/components/providers/ConfigProvider";
import { HOME_DESCRIPTION, HOME_TITLE, SITE_NAME, SITE_URL } from "@/lib/site";
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

// Site-wide defaults only. No `alternates.canonical` and no `openGraph.url`
// here — both are inherited by every route that doesn't set its own, which
// would declare every page a duplicate of the homepage (see lib/site.ts).
// Routes set theirs through `pageMetadata()`.
export const metadata: Metadata = {
  // Without this, Next resolves relative metadata URLs — including the
  // file-based opengraph-image every route inherits — against
  // http://localhost:3000 in a self-hosted production build, so every
  // social preview pointed at an address no crawler can reach.
  metadataBase: new URL(SITE_URL),
  title: {
    default: HOME_TITLE,
    template: `%s — ${SITE_NAME}`,
  },
  description: HOME_DESCRIPTION,
  applicationName: SITE_NAME,
  openGraph: {
    siteName: SITE_NAME,
    locale: "en_US",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
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
        `font-mono` is Share Tech Mono. It governs every element that does not
        name a font itself, which is most of the site's prose.

        It used to be VT323, and that face is now gone from the bundle rather
        than merely unused — it had zero call sites left but was still being
        fetched from Google Fonts on every page load. Why it went, measured
        from the shipped woff2 files:

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
        /* No `antialiased`.

           `-webkit-font-smoothing: antialiased` THINS glyphs. On a light-on-
           dark page that is the wrong direction: the stems of an already-thin
           monospace get thinner still, which is a third of why a reader
           reported the grey text as hard to read (the other two thirds being
           the ink ramp and 12px). Letting the platform use its default
           smoothing renders the same text perceptibly heavier at zero layout
           cost. */
        className={`${archivo.variable} ${pressStart.variable} ${shareTech.variable} font-mono`}
      >
        <ConfigProvider>
          <a
            href="#main-content"
            className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:top-2 focus-visible:left-2 focus-visible:z-[10000] focus-visible:bg-surface-base focus-visible:text-ink-hi focus-visible:border-2 focus-visible:border-phos/40 focus-visible:px-4 focus-visible:py-2 focus-visible:text-lg focus-visible:font-mono"
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
