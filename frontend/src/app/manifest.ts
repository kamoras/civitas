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
    // surface.base, as in icon.svg.
    background_color: "#0e0c0a",
    theme_color: "#0e0c0a",
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml" },
      { src: "/apple-icon.png", sizes: "192x192", type: "image/png" },
    ],
  };
}
