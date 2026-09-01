import { ImageResponse } from "next/og";
import { NextRequest } from "next/server";

export const runtime = "nodejs";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Raw hex, not the Tailwind tokens used elsewhere on the site — satori
// (next/og's renderer) doesn't resolve Tailwind classes or CSS custom
// properties, only inline style objects, so these are duplicated here
// from tailwind.config.ts's dem-blue/signal-red/ind-purple.
const PARTY_ACCENT: Record<string, string> = {
  D: "#82acff",
  R: "#ff8989",
  I: "#c995ff",
};

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
      <span style={{ color: "#00ff41", fontSize: 18, letterSpacing: 6 }}>CIVITAS</span>
      <span style={{ color: "#333", fontSize: 18 }}>|</span>
      <span style={{ color: "#555", fontSize: 14, letterSpacing: 4 }}>{section}</span>
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
        borderTop: "1px solid #1e1e1e",
        paddingTop: 28,
      }}
    >
      <span style={{ color: "#00ff41", fontSize: 14, letterSpacing: 2 }}>
        civitas-research.org
      </span>
      <span style={{ color: "#333", fontSize: 13, letterSpacing: 1 }}>{label}</span>
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

  // Only ever set from a source article whose feed explicitly granted
  // redistribution rights (see backend news_feeds._rights_cleared_image_url)
  // — same fetch-and-inline treatment as a politician's headshot, since
  // there's no guarantee this URL is still live months after the source
  // article ran.
  const photoDataUri = issue?.imageUrl ? await fetchPhotoAsDataUri(issue.imageUrl) : null;

  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: "#0a0a0a",
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: "monospace",
        border: "1px solid #1a1a1a",
      }}
    >
      <Header section="ACTION CENTER" />
      <div style={{ display: "flex", alignItems: "center", gap: 40, flex: 1, marginBottom: 32 }}>
        <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
          <div
            style={{
              color: "#e8e8e8",
              fontSize: title.length > 60 ? 38 : 46,
              fontWeight: 700,
              lineHeight: 1.2,
              marginBottom: 32,
            }}
          >
            {title}
          </div>
          <div style={{ color: "#888", fontSize: 22, lineHeight: 1.5 }}>
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
            style={{ objectFit: "cover", border: "1px solid #1a1a1a" }}
          />
        )}
      </div>
      <Footer label="PUBLIC FEDERAL DATA" />
    </div>,
    { width: 1200, height: 630 }
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
  const accent = PARTY_ACCENT[party] ?? "#00ff41";
  const standing = formatStanding(identity);
  const overall = profile?.overallScore != null ? profile.overallScore.toFixed(1) : null;

  const photoDataUri = identity?.thumbnailUrl
    ? await fetchPhotoAsDataUri(identity.thumbnailUrl)
    : null;

  return new ImageResponse(
    <div
      style={{
        width: 1200,
        height: 630,
        background: "#0a0a0a",
        display: "flex",
        flexDirection: "column",
        padding: "60px 72px",
        fontFamily: "monospace",
        border: "1px solid #1a1a1a",
      }}
    >
      <Header section={identity?.role?.toUpperCase() ?? "PUBLIC RECORD"} />
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
          <div style={{ color: "#e8e8e8", fontSize: 52, fontWeight: 700, lineHeight: 1.2 }}>
            {name}
          </div>
          {standing && (
            <div style={{ color: accent, fontSize: 28, marginTop: 12, letterSpacing: 2 }}>
              {standing}
            </div>
          )}
          {overall && (
            <div style={{ display: "flex", color: "#888", fontSize: 24, marginTop: 24 }}>
              {`Civitas score: ${overall}/100`}
            </div>
          )}
        </div>
      </div>
      <Footer label="REPRESENTATION SCORE" />
    </div>,
    { width: 1200, height: 630 }
  );
}

export async function GET(req: NextRequest) {
  const rawPoliticianId = req.nextUrl.searchParams.get("politician");
  // Politician ids are slugs (e.g. "chuck-grassley"), unlike the issue
  // route's numeric id — validated here rather than passed unvalidated
  // into the outgoing fetch URL.
  const politicianId =
    rawPoliticianId && /^[a-z0-9-]+$/.test(rawPoliticianId) ? rawPoliticianId : null;
  if (politicianId) {
    const profile = await fetchPolitician(politicianId);
    return politicianImage(profile);
  }

  const rawIssueId = req.nextUrl.searchParams.get("issue");
  const issueId = rawIssueId && /^\d+$/.test(rawIssueId) ? rawIssueId : null;
  const issue = issueId ? await fetchIssue(issueId) : null;
  return issueImage(issue);
}
