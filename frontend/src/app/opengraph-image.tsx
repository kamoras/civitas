import { ImageResponse } from "next/og";

export const runtime = "edge";
export const alt = "CIVITAS // PUBLIC RECORD";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OgImage() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "#0d0208",
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "monospace",
          padding: "60px",
          position: "relative",
        }}
      >
        {/* Scanline effect strip */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "4px",
            background: "#00ff41",
            opacity: 0.6,
          }}
        />
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            height: "4px",
            background: "#00ff41",
            opacity: 0.6,
          }}
        />

        {/* Border frame */}
        <div
          style={{
            position: "absolute",
            top: "24px",
            left: "24px",
            right: "24px",
            bottom: "24px",
            border: "1px solid rgba(0,255,65,0.25)",
            display: "flex",
          }}
        />

        {/* Content */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "28px",
          }}
        >
          {/* Title */}
          <div
            style={{
              fontSize: "72px",
              color: "#00ff41",
              letterSpacing: "0.15em",
              fontWeight: "bold",
              lineHeight: 1,
            }}
          >
            CIVITAS
          </div>

          {/* Divider */}
          <div
            style={{
              width: "360px",
              height: "1px",
              background: "rgba(0,255,65,0.3)",
              display: "flex",
            }}
          />

          {/* Subtitle */}
          <div
            style={{
              fontSize: "22px",
              color: "rgba(0,255,65,0.55)",
              letterSpacing: "0.2em",
              textAlign: "center",
            }}
          >
            PUBLIC RECORD
          </div>

          {/* Description */}
          <div
            style={{
              fontSize: "18px",
              color: "rgba(0,255,65,0.4)",
              letterSpacing: "0.05em",
              textAlign: "center",
              maxWidth: "680px",
              lineHeight: 1.5,
            }}
          >
            Congressional scorecards · Campaign finance transparency · Civic actions
          </div>

          {/* Domain badge */}
          <div
            style={{
              marginTop: "12px",
              border: "1px solid rgba(0,255,65,0.2)",
              padding: "8px 24px",
              fontSize: "14px",
              color: "rgba(0,255,65,0.3)",
              letterSpacing: "0.2em",
            }}
          >
            civitas-research.org
          </div>
        </div>
      </div>
    ),
    { ...size }
  );
}
