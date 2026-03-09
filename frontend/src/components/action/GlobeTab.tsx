"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import Globe from "react-globe.gl";
import { fetchCountryNews } from "@/lib/api";
import { safeHref } from "@/lib/formatting";
import type { CountryNews } from "@/lib/api";

export default function GlobeTab() {
  const [countries, setCountries] = useState<CountryNews[]>([]);
  const [selected, setSelected] = useState<CountryNews | null>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const globeRef = useRef<any>(null);
  const detailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchCountryNews()
      .then((data) => setCountries(data.countries))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (globeRef.current) {
      const controls = globeRef.current.controls();
      if (controls) {
        controls.autoRotate = true;
        controls.autoRotateSpeed = 0.5;
        controls.enableZoom = true;
      }
      globeRef.current.pointOfView({ lat: 30, lng: -20, altitude: 2.2 }, 0);
    }
  }, [loading]);

  const scrollToDetail = useCallback(() => {
    setTimeout(() => {
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 300);
  }, []);

  const handlePointClick = useCallback(
    (point: object) => {
      const p = point as { country: string };
      const country = countries.find((c) => c.country === p.country);
      if (country) {
        setSelected(country);
        scrollToDetail();
        if (globeRef.current) {
          const controls = globeRef.current.controls();
          if (controls) controls.autoRotate = false;
          globeRef.current.pointOfView(
            { lat: country.lat, lng: country.lng, altitude: 1.8 },
            800,
          );
        }
      }
    },
    [countries, scrollToDetail],
  );

  const pointsData = countries.map((c) => ({
    lat: c.lat,
    lng: c.lng,
    country: c.country,
    size: Math.min(0.4 + c.articleCount * 0.15, 1.2),
    color:
      c.articleCount >= 5
        ? "#ff4444"
        : c.articleCount >= 3
          ? "#ff9900"
          : c.articleCount >= 2
            ? "#00ffcc"
            : "#44ff44",
  }));

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-neon-cyan animate-pulse font-pixel text-sm">
          {">"} MAPPING GLOBAL RELATIONS...
        </div>
      </div>
    );
  }

  if (countries.length === 0) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-matrix-green/50">No international news found in current feeds.</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="text-center text-[10px] text-matrix-green/40 font-pixel mb-2">
        CLICK OR TAP A POINT TO SEE U.S.-RELATED NEWS FOR THAT COUNTRY
      </div>

      <div className="relative w-full flex justify-center" style={{ height: "500px" }}>
        <Globe
          ref={globeRef}
          width={Math.min(typeof window !== "undefined" ? window.innerWidth - 32 : 800, 800)}
          height={500}
          globeImageUrl="//unpkg.com/three-globe/example/img/earth-night.jpg"
          backgroundImageUrl="//unpkg.com/three-globe/example/img/night-sky.png"
          pointsData={pointsData}
          pointLat="lat"
          pointLng="lng"
          pointAltitude={0.01}
          pointRadius="size"
          pointColor="color"
          pointLabel={(d: object) => { const p = d as { country: string }; return `<div style="font-family:monospace;font-size:11px;color:#00ff41;background:rgba(0,0,0,0.85);padding:6px 10px;border:1px solid #00ff4133;border-radius:2px"><b>${p.country}</b><br/>${countries.find((c) => c.country === p.country)?.articleCount || 0} articles</div>`; }}
          onPointClick={handlePointClick}
          atmosphereColor="#00ff41"
          atmosphereAltitude={0.15}
        />
      </div>

      {selected && (
        <div
          ref={detailRef}
          className="terminal-window border-t-2 border-t-green-400/50 p-5 sm:p-6 scroll-mt-4"
          role="region"
          aria-label={`U.S. and ${selected.country} news`}
        >
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-pixel text-base sm:text-lg text-green-400">
              U.S. &amp; {selected.country}
            </h3>
            <button
              onClick={() => {
                setSelected(null);
                if (globeRef.current) {
                  const controls = globeRef.current.controls();
                  if (controls) controls.autoRotate = true;
                }
              }}
              className="text-matrix-green/40 hover:text-matrix-green font-pixel text-xs"
              aria-label="Close country detail panel"
            >
              [CLOSE]
            </button>
          </div>
          <div className="text-[10px] text-matrix-green/40 mb-4">
            {selected.articleCount} article{selected.articleCount !== 1 ? "s" : ""} from recent news
          </div>
          <div className="space-y-3">
            {selected.articles.map((article, i) => (
              <div
                key={i}
                className="border-l-2 border-l-green-400/30 pl-3"
              >
                <a
                  href={safeHref(article.url) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-matrix-green/80 hover:text-neon-cyan transition-colors leading-relaxed"
                >
                  {article.title}
                </a>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-[10px] text-matrix-green/30">{article.source}</span>
                  {article.date && (
                    <span className="text-[10px] text-matrix-green/20">
                      {article.date.split("T")[0]}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!selected && (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
          {countries.slice(0, 12).map((c) => (
            <button
              key={c.country}
              onClick={() => {
                setSelected(c);
                scrollToDetail();
                if (globeRef.current) {
                  const controls = globeRef.current.controls();
                  if (controls) controls.autoRotate = false;
                  globeRef.current.pointOfView(
                    { lat: c.lat, lng: c.lng, altitude: 1.8 },
                    800,
                  );
                }
              }}
              className="terminal-window p-2.5 text-left hover:border-green-400/30 transition-colors"
              aria-label={`View news about ${c.country}`}
            >
              <div className="font-pixel text-[10px] text-green-400/80 truncate">
                {c.country}
              </div>
              <div className="text-[10px] text-matrix-green/40 mt-0.5">
                {c.articleCount} article{c.articleCount !== 1 ? "s" : ""}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
