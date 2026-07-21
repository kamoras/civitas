"use client";

import { useEffect, useState } from "react";
import { fetchSenatorHistory, fetchRepresentativeHistory, fetchPresidentHistory } from "@/lib/api";
import type { ScoreSnapshot } from "@/lib/api";
import ScoreTrend from "./ScoreTrend";

interface ScoreTrendSectionProps {
  entityId: string;
  entityType: "senate" | "house" | "president";
}

const FETCHERS = {
  senate: fetchSenatorHistory,
  house: fetchRepresentativeHistory,
  president: fetchPresidentHistory,
} as const;

export default function ScoreTrendSection({ entityId, entityType }: ScoreTrendSectionProps) {
  const [snapshots, setSnapshots] = useState<ScoreSnapshot[]>([]);

  useEffect(() => {
    FETCHERS[entityType](entityId)
      .then((data) => setSnapshots(data.snapshots))
      .catch(() => {});
  }, [entityId, entityType]);

  return <ScoreTrend snapshots={snapshots} />;
}
