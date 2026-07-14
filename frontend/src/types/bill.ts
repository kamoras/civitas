export interface BillInFlight {
  billId: string;
  title: string;
  chamber: "senate" | "house";
  sponsorId: string;
  sponsorName: string;
  sponsorParty: "D" | "R" | "I";
  sponsorState: string;
  sponsorThumbnailUrl: string | null;
  introducedDate: string;
  latestAction: string;
  latestActionDate: string;
  stage: string;
  policyArea: string;
  congress: number;
  billType: string;
  isLaw: boolean;
}

export interface PaginatedBills {
  bills: BillInFlight[];
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
  stageCounts: Record<string, number>;
}
