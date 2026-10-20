import type { MetadataRoute } from "next";
import { HOME_DESCRIPTION, SITE_NAME } from "@/lib/site";

// The web app manifest (/manifest.webmanifest, linked from every page by
// Next). Another place the full name is stated, beside the title template
// and og:site_name; "Civitas" is the short name a home screen has room for.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: SITE_NAME,
    short_name: "Civitas",
    description: HOME_DESCRIPTION,
    start_url: "/",
    display: "browser",
    // Parchment, the light theme's ground (the layout's theme-color meta
    // switches to carbon for a dark system; a manifest takes one colour).
    background_color: "#f5efe2",
    theme_color: "#f5efe2",
    // apple-icon.png and icon-512.png are icon.svg rendered (icons.test.ts).
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml" },
      { src: "/apple-icon.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
