"use client";

import { useEffect, useRef, useState } from "react";
import { MAP_LABEL_HALO } from "@/lib/results";
import { stateLabelUnits, type HouseBlock, type TileSeat } from "@/lib/electionMap";

/** A House tile map's state letters, over each block (houseLayout), drawn
 * at STATE_LABEL_PX at whatever width the map renders, or not at all where
 * that would not fit (stateLabelUnits): a fixed size in the map's units was
 * 8.3px at its widest. Every state stays named by the map's readout and,
 * on a phone, the state list. Measured from the SVG it sits in, so a
 * server-rendered map can carry it. */
export default function HouseStateLabels<T extends TileSeat>({
  blocks,
  viewBoxWidth,
}: {
  blocks: HouseBlock<T>[];
  viewBoxWidth: number;
}) {
  const ref = useRef<SVGGElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    const svg = ref.current?.ownerSVGElement;
    if (!svg || typeof ResizeObserver === "undefined") return;
    const update = () => setWidth(svg.getBoundingClientRect().width || null);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);
  const units = stateLabelUnits(width, viewBoxWidth);
  return (
    <g ref={ref} aria-hidden="true">
      {units !== null &&
        blocks.map((b) => (
          <text
            key={b.state}
            x={b.x}
            y={b.y - 2}
            fontSize={Math.ceil(units * 100) / 100 /* up, never under 13px */}
            className={`pointer-events-none fill-ink-min font-mono ${MAP_LABEL_HALO}`}
          >
            {b.state}
          </text>
        ))}
    </g>
  );
}
