"use client";

import { useEffect, useState } from "react";
import { fetchSenatorHistory, fetchRepresentativeHistory } from "@/lib/api";
import type { ScoreSnapshot } from "@/lib/api";
import ScoreTrend from "./ScoreTrend";

interface ScoreTrendSectionProps {
  entityId: string;
  entityType: "senate" | "house";
}

export default function ScoreTrendSection({ entityId, entityType }: ScoreTrendSectionProps) {
  const [snapshots, setSnapshots] = useState<ScoreSnapshot[]>([]);

  useEffect(() => {
    const fn = entityType === "house" ? fetchRepresentativeHistory : fetchSenatorHistory;
    fn(entityId)
      .then((data) => setSnapshots(data.snapshots))
      .catch(() => {});
  }, [entityId, entityType]);

  return <ScoreTrend snapshots={snapshots} />;
}
