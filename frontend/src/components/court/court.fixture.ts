import type { Court, CourtJustice } from "@/types/court";

/** Made-up justices: the Court's membership comes from the data. */
export function justice(id: string, position: number | null): CourtJustice {
  return {
    id,
    name: `${id} Example`,
    lastName: id,
    roleTitle: "Associate Justice",
    appointingPresident: null,
    appointingParty: null,
    dateStart: "2010-01-01",
    photo: null,
    mq:
      position === null
        ? null
        : { term: 2024, position, low: position - 0.5, high: position + 0.5 },
  };
}

export function court(over: Partial<Court> = {}): Court {
  return {
    justices: [justice("Ames", -2), justice("Brook", 0.3), justice("Cole", 2)],
    mqTerm: 2024,
    median: ["Brook"],
    record: {
      cases: 4,
      unanimous: 2,
      oneVote: 1,
      unanimousPct: 50,
      oneVotePct: 25,
      terms: [2024, 2025],
    },
    currentTerm: { term: 2026, decided: 0 },
    agreement: [
      { a: "Ames", b: "Brook", agree: 3, both: 4, share: 75 },
      { a: "Ames", b: "Cole", agree: 2, both: 4, share: 50 },
      { a: "Brook", b: "Cole", agree: 3, both: 3, share: 100 },
    ],
    lineups: {
      splits: ["3-0", "2-1"],
      terms: [
        { term: 2024, cases: 2, counts: { "3-0": 1, "2-1": 1 } },
        { term: 2025, cases: 2, counts: { "3-0": 1, "2-1": 1 } },
      ],
    },
    decisions: {
      total: 1,
      page: 1,
      perPage: 10,
      decisions: [
        {
          caseId: "scotus-2025-25-1",
          name: "Example v. Case",
          term: "2025",
          decided: "2026-06-30",
          majorityVotes: 2,
          minorityVotes: 1,
          split: "2-1",
          unanimous: false,
          majority: ["Brook", "Cole"],
          minority: ["Ames"],
          author: "Cole",
          links: {
            oyez: "https://www.oyez.org/cases/2025/25-1",
            opinion: null,
            explore: "/explore/7",
          },
        },
      ],
    },
    ...over,
  };
}
