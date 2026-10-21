import type { Metadata, Viewport } from "next";
import ConfigProvider from "@/components/providers/ConfigProvider";
import LoadTimingBeacon from "@/components/LoadTimingBeacon";
import NavigationBeacon from "@/components/NavigationBeacon";
import ForgetLegacyStorage from "@/components/ForgetLegacyStorage";
import {
  FEED_ALTERNATES,
  HOME_DESCRIPTION,
  HOME_TITLE,
  SITE_NAME,
  SITE_URL,
  TITLE_TEMPLATE,
} from "@/lib/site";
import { fontVariables } from "./fonts";
import "./fonts/fallback.css";
import "./globals.css";
import { THEME_BOOT } from "@/components/layout/ThemeToggle";

// Site-wide defaults only. No `alternates.canonical` and no `openGraph.url`
// here — both are inherited by every route that doesn't set its own, which
// would declare every page a duplicate of the homepage (see lib/site.ts).
// Routes set theirs through `pageMetadata()`.
// The browser chrome takes the page's ground: parchment, or carbon when the
// system is dark (globals.css's --surface-base). A per-tab theme toggle
// doesn't move it; the meta follows the system setting only.
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5efe2" },
    { media: "(prefers-color-scheme: dark)", color: "#14120f" },
  ],
};

export const metadata: Metadata = {
  // Without this, Next resolves relative metadata URLs — including the
  // file-based opengraph-image every route inherits — against
  // http://localhost:3000 in a self-hosted production build, so every
  // social preview pointed at an address no crawler can reach.
  metadataBase: new URL(SITE_URL),
  title: {
    default: HOME_TITLE,
    template: TITLE_TEMPLATE,
  },
  description: HOME_DESCRIPTION,
  applicationName: SITE_NAME,
  // The feed only — never a canonical here (see above).
  alternates: { types: FEED_ALTERNATES },
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
    // data-theme is set by THEME_BOOT before hydration when this tab chose a
    // theme, so React is told the attribute may differ from the server's.
    //
    // No <head> element here (2026-10). The flight payload sends a head's
    // children as a lazy reference; when that chunk hadn't arrived as
    // hydration reached <head>, React suspended there and replayed the
    // head, entering its hydration scope twice, so <body> was hydrated
    // against the head's first child: React error #418 on a share of loads
    // of every page, through Cloudflare's streaming. React makes the head
    // itself for the hoisted metadata, and the theme script runs first
    // thing in <body>, still before anything is painted.
    <html lang="en" suppressHydrationWarning>
      {/*
        `font-sans` (Fira Sans) governs every element that does not name a
        font itself, which is most of the site's prose. It was `font-mono`
        (Share Tech Mono, and VT323 before it): prose set in a monospace was
        the main reason the site read as a terminal (redesign spec 1.4).
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
        className="font-sans"
        style={fontVariables}
      >
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
        <ConfigProvider>
          <a
            href="#main-content"
            className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:top-2 focus-visible:left-2 focus-visible:z-[10000] focus-visible:bg-surface-base focus-visible:text-ink-hi focus-visible:border-2 focus-visible:border-phos/40 focus-visible:px-4 focus-visible:py-2 focus-visible:text-lg focus-visible:font-mono"
          >
            Skip to main content
          </a>
          {children}
          <LoadTimingBeacon />
          <NavigationBeacon />
          <ForgetLegacyStorage />
        </ConfigProvider>
      </body>
    </html>
  );
}
