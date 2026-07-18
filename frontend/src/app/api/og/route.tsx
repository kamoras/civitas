import { ImageResponse } from "next/og";
import { NextRequest } from "next/server";

export const runtime = "nodejs";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

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

export async function GET(req: NextRequest) {
  const rawId = req.nextUrl.searchParams.get("issue");
  // The backend route takes an int path param and would reject anything
  // else anyway, but validating here (rather than passing the query value
  // straight into the outgoing fetch URL) keeps this handler from ever
  // building a request URL out of unvalidated user input.
  const id = rawId && /^\d+$/.test(rawId) ? rawId : null;
  const issue = id ? await fetchIssue(id) : null;

  const title = issue?.title ?? "Civitas Action Center";
  const summary = issue?.summary
    ? issue.summary.length > 140
      ? issue.summary.slice(0, 137) + "…"
      : issue.summary
    : "Track what Congress is doing — and what you can do about it.";

  return new ImageResponse(
    (
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
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            marginBottom: 48,
          }}
        >
          <span style={{ color: "#00ff41", fontSize: 18, letterSpacing: 6 }}>
            CIVITAS
          </span>
          <span style={{ color: "#333", fontSize: 18 }}>|</span>
          <span style={{ color: "#555", fontSize: 14, letterSpacing: 4 }}>
            ACTION CENTER
          </span>
        </div>

        {/* Title */}
        <div
          style={{
            color: "#e8e8e8",
            fontSize: title.length > 60 ? 38 : 46,
            fontWeight: 700,
            lineHeight: 1.2,
            marginBottom: 32,
            flex: 1,
          }}
        >
          {title}
        </div>

        {/* Summary */}
        <div
          style={{
            color: "#888",
            fontSize: 22,
            lineHeight: 1.5,
            marginBottom: 48,
          }}
        >
          {summary}
        </div>

        {/* Footer */}
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
          <span style={{ color: "#333", fontSize: 13, letterSpacing: 1 }}>
            PUBLIC FEDERAL DATA
          </span>
        </div>
      </div>
    ),
    { width: 1200, height: 630 }
  );
}
