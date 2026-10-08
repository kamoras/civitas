"use client";

import { useEffect, useState } from "react";
import { fetchSenatorHistory, fetchRepresentativeHistory, fetchPresidentHistory } from "@/lib/api";
import type { ScoreHistory } from "@/lib/api";
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
  const [history, setHistory] = useState<ScoreHistory>({ snapshots: [] });

  useEffect(() => {
    FETCHERS[entityType](entityId)
      .then(setHistory)
      .catch(() => {});
  }, [entityId, entityType]);

  return <ScoreTrend snapshots={history.snapshots} change={history.change} />;
}
