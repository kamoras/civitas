import { ImageResponse } from "next/og";
import { NextRequest } from "next/server";
import { loadArchivoBold } from "@/lib/ogFonts";

export const runtime = "nodejs";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Raw hex, not the Tailwind tokens used elsewhere on the site — Satori
// (next/og's renderer) doesn't resolve Tailwind classes or CSS custom
// properties, only inline style objects, so these are hand-copied from
// tailwind.config.ts. This route originally shipped with the PRE-REBRAND
// green-terminal look (#00ff41 as structural text, plain "monospace",
// ad-hoc greys) — tailwind.config.ts's own comments document that look as
// retired ("the migration finished with zero call sites left"), and this
// route was simply never updated when the rest of the site was. Fixed
// live 2026-09-02 after a report that OG images "follow our old theme."
const INK_HI = "#F2EEE7";
const INK = "#E3DCD1";
const INK_LO = "#CDC7BC";
const INK_MIN = "#BBB5AC";
const SURFACE_BASE = "#0E0C0A";
// Same 7%-white hairline used for every card/divider border on the site
// (Tailwind's border-white/[0.07]) — not an ink-tinted line.
const HAIRLINE = "rgba(255,255,255,0.07)";
const DOMAIN = "civitas-research.org";

const PARTY_ACCENT: Record<string, string> = {
  D: "#82acff",
  R: "#ff8989",
  I: "#c995ff",
};

// Mirrors lib/representation.ts's getScoreColor exactly (same 81/61/41/21
// tiers) — that file returns a Tailwind class name for DOM rendering,
// which Satori can't consume, so this is the hex-returning equivalent.
// Keep these two in sync: the whole point of a single shared scoring
// function elsewhere in the app was stopping the same score from
// rendering a different tier depending on which page shows it.
function scoreColorHex(score: number): string {
  if (score >= 81) return "#00FF41"; // phos
  if (score >= 61) return "#4DE3E8"; // signal.cyan
  if (score >= 41) return "#FFD84D"; // signal.amber
  if (score >= 21) return "#FF8A3D"; // signal.orange
  return "#FF8989"; // signal.red
}

async function fetchIssue(id: string) {
  try {
    const res = await fetch(`${BACKEND}/api/action/issues/${id}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// bioguide.congress.gov sits behind Cloudflare bot-mitigation that blocks
// a plain HEAD request outright (confirmed live) and challenges a GET
// that self-identifies as a bot/non-browser client — a browser-shaped
// User-Agent gets a normal 200, confirmed live, so that's used here
// rather than a self-identifying one. This fetches the actual bytes
// (there's no cheaper existence check that reliably works against this
// host) and inlines them as a data URI rather than leaving the img src
// pointing at the remote URL, since satori's own internal fetch for a
// remote <img> src isn't guaranteed to behave any better than a HEAD
// would. A 5s timeout keeps a slow/hanging host (also observed live)
// from stalling the whole OG image instead of just dropping the photo.
async function fetchPhotoAsDataUri(url: string): Promise<string | null> {
  try {
    const res = await fetch(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      },
      signal: AbortSignal.timeout(5000),
    });
    const contentType = res.headers.get("content-type") ?? "";
    if (!res.ok || !contentType.startsWith("image/")) return null;
    const bytes = Buffer.from(await res.arrayBuffer());
    return `data:${contentType};base64,${bytes.toString("base64")}`;
  } catch {
    return null;
  }
}

async function fetchPolitician(id: string) {
  try {
    const res = await fetch(`${BACKEND}/api/politicians/${id}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function Header({ section }: { section: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 48 }}>
      <span style={{ color: INK_HI, fontSize: 18, letterSpacing: 6 }}>CIVITAS</span>
      <span style={{ color: INK_MIN, fontSize: 18 }}>|</span>
      <span style={{ color: INK_LO, fontSize: 14, letterSpacing: 4 }}>{section}</span>
    </div>
  );
}

function Footer({ label }: { label: string }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        borderTop: `1px solid ${HAIRLINE}`,
        paddingTop: 28,
      }}
    >
      <span style={{ color: INK_LO, fontSize: 14, letterSpacing: 2 }}>
        {DOMAIN}
      </span>
      <span style={{ color: INK_MIN, fontSize: 13, letterSpacing: 1 }}>{label}</span>
    </div>
  );
}

async function issueImage(
  issue: { title?: string; summary?: string; imageUrl?: string | null } | null
) {
  const title = issue?.title ?? "Civitas Action Center";
  const summary = issue?.summary
    ? issue.summary.length > 140
      ? issue.summary.slice(0, 137) + "…"
      : issue.summary
    : "Track what Congress is doing — and what you can do about it.";
  const section = "ACTION CENTER";
  const footerLabel = "PUBLIC FEDERAL DATA";

  // Only ever set from a source article whose feed explicitly granted
  // redistribution rights (see backend news_feeds._rights_cleared_image)
  // — same fetch-and-inline treatment as a politician's headshot, since
  // there's no guarantee this URL is still live months after the source
  // article ran.
  const photoDataUri = issue?.imageUrl ? await fetchPhotoAsDataUri(issue.imageUrl) : null;

  // See lib/ogFonts.ts: subset text must cover every string actually
  // rendered below, or the leftover characters fall back to Satori's own
  // default face instead of Archivo.
  let archivoBold: ArrayBuffer | null = null;
  try {
    archivoBold = await loadArchivoBold(
      `CIVITAS${section}${title}${summary}${DOMAIN}${footerLabel}|`
    );
  } catch {
    // fall through with archivoBold still null — degrade to Satori's
    // default face rather than fail the whole image over a font fetch.
  }

  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: "Archivo",
        border: `1px solid ${HAIRLINE}`,
      }}
    >
      <Header section={section} />
      <div style={{ display: "flex", alignItems: "center", gap: 40, flex: 1, marginBottom: 32 }}>
        <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
          <div
            style={{
              color: INK_HI,
              fontSize: title.length > 60 ? 38 : 46,
              fontWeight: 700,
              lineHeight: 1.2,
              marginBottom: 32,
            }}
          >
            {title}
          </div>
          <div style={{ color: INK, fontSize: 22, lineHeight: 1.5 }}>
            {summary}
          </div>
        </div>
        {photoDataUri && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={photoDataUri}
            alt=""
            width={320}
            height={320}
            style={{ objectFit: "cover", border: `1px solid ${HAIRLINE}` }}
          />
        )}
      </div>
      <Footer label={footerLabel} />
    </div>,
    {
      width: 1200,
      height: 630,
      ...(archivoBold
        ? { fonts: [{ name: "Archivo", data: archivoBold, weight: 700 as const, style: "normal" as const }] }
        : {}),
    }
  );
}

export type OgPoliticianIdentity = {
  name?: string; party?: string; state?: string; district?: number | null;
  role?: string; thumbnailUrl?: string | null;
};

// A truthy check on district treats an at-large seat (0, FEC's own "00"
// convention — see lib/elections.ts's districtToken) the same as "no
// district" (a Senate identity), silently rendering it in the Senate's
// format — this exact bug class already has a named regression test in
// lib/elections.test.ts.
export function formatStanding(identity: OgPoliticianIdentity | undefined): string {
  if (identity?.district != null) {
    const district = identity.district === 0 ? "AL" : identity.district;
    return `${identity.party}-${identity.state}-${district}`;
  }
  if (identity?.state) return `${identity.party}-${identity.state}`;
  return "";
}

async function politicianImage(profile: {
  identity?: OgPoliticianIdentity;
  overallScore?: number | null;
} | null) {
  const identity = profile?.identity;
  const name = identity?.name ?? "Civitas";
  const party = identity?.party ?? "";
  const accent = PARTY_ACCENT[party] ?? "#F2EEE7";
  const standing = formatStanding(identity);
  const overallScore = profile?.overallScore ?? null;
  const overall = overallScore != null ? overallScore.toFixed(1) : null;
  const scoreLine = overall ? `Civitas score: ${overall}/100` : "";
  const section = identity?.role?.toUpperCase() ?? "PUBLIC RECORD";
  const footerLabel = "REPRESENTATION SCORE";

  const photoDataUri = identity?.thumbnailUrl
    ? await fetchPhotoAsDataUri(identity.thumbnailUrl)
    : null;

  // See lib/ogFonts.ts: subset text must cover every string actually
  // rendered below, or the leftover characters fall back to Satori's own
  // default face instead of Archivo.
  let archivoBold: ArrayBuffer | null = null;
  try {
    archivoBold = await loadArchivoBold(
      `CIVITAS${section}${name}${standing}${scoreLine}${DOMAIN}${footerLabel}|`
    );
  } catch {
    // fall through with archivoBold still null — degrade to Satori's
    // default face rather than fail the whole image over a font fetch.
  }

  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: SURFACE_BASE,
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: "Archivo",
        border: `1px solid ${HAIRLINE}`,
      }}
    >
      <Header section={section} />
      <div style={{ display: "flex", alignItems: "center", gap: 48, flex: 1 }}>
        {photoDataUri && (
          // satori renders this tree standalone at request time; next/image's
          // client-side optimization runtime doesn't apply here.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={photoDataUri}
            alt={name}
            width={280}
            height={280}
            style={{ objectFit: "cover", border: `2px solid ${accent}` }}
          />
        )}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ color: INK_HI, fontSize: 52, fontWeight: 700, lineHeight: 1.2 }}>
            {name}
          </div>
          {standing && (
            <div style={{ color: accent, fontSize: 28, marginTop: 12, letterSpacing: 2 }}>
              {standing}
            </div>
          )}
          {scoreLine && overallScore != null && (
            <div
              style={{
                display: "flex",
                color: scoreColorHex(overallScore),
                fontSize: 24,
                marginTop: 24,
              }}
            >
              {scoreLine}
            </div>
          )}
        </div>
      </div>
      <Footer label={footerLabel} />
    </div>,
    {
      width: 1200,
      height: 630,
      ...(archivoBold
        ? { fonts: [{ name: "Archivo", data: archivoBold, weight: 700 as const, style: "normal" as const }] }
        : {}),
    }
  );
}

// Every real link on the site points at an issue's public id (backend
// issue_ids.py: "i" + 8 lowercase hex chars, e.g. "i9e3779b1") via
// issue.publicId — page.tsx's generateMetadata builds this OG URL from
// that same route param. A digits-only check here rejected every real
// request and silently fell through to the generic fallback card for
// every issue, photo or not; a bare numeric id is only ever a legacy
// pre-public-id share link. Backend's own get_action_issue accepts both
// formats (see its docstring) — mirrored here, not invented.
export function parseIssueId(rawIssueId: string | null): string | null {
  return rawIssueId && /^(i[0-9a-f]{8}|\d+)$/i.test(rawIssueId) ? rawIssueId : null;
}

export async function GET(req: NextRequest) {
  const rawPoliticianId = req.nextUrl.searchParams.get("politician");
  // Politician ids are slugs (e.g. "chuck-grassley"), unlike the issue
  // route's id format — validated here rather than passed unvalidated
  // into the outgoing fetch URL.
  const politicianId =
    rawPoliticianId && /^[a-z0-9-]+$/.test(rawPoliticianId) ? rawPoliticianId : null;
  if (politicianId) {
    const profile = await fetchPolitician(politicianId);
    return politicianImage(profile);
  }

  const issueId = parseIssueId(req.nextUrl.searchParams.get("issue"));
  const issue = issueId ? await fetchIssue(issueId) : null;
  return issueImage(issue);
}
