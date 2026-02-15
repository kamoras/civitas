import { log } from "../config.mjs";

const VALID_INDUSTRIES = new Set([
  "PHARMA",
  "INSURANCE",
  "OIL_GAS",
  "DEFENSE",
  "FINANCE",
  "REAL_ESTATE",
  "TECH",
  "TELECOM",
  "AGRIBUSINESS",
  "ENERGY",
  "CONSTRUCTION",
  "TRANSPORT",
  "LAWYERS",
  "LOBBYISTS",
  "GAMBLING",
  "GUNS",
  "TOBACCO",
  "CRYPTO",
  "PRIVATE_PRISON",
  "OTHER",
]);

const VALID_PARTIES = new Set(["D", "R", "I"]);
const VALID_VOTES = new Set(["Yea", "Nay", "Not Voting"]);

/**
 * Validate and fix a senator record to match the Senator type.
 * @param {Object} senator - Assembled senator record
 * @returns {Object} Validated senator record (mutated in place)
 */
export function validateSenator(senator) {
  const warnings = [];

  // Basic fields
  if (!senator.id) warnings.push("Missing id");
  if (!senator.name) warnings.push("Missing name");
  if (!senator.state || senator.state.length !== 2)
    warnings.push(`Invalid state: ${senator.state}`);
  if (!VALID_PARTIES.has(senator.party)) {
    warnings.push(`Invalid party: ${senator.party}, defaulting to I`);
    senator.party = "I";
  }
  if (typeof senator.yearsInOffice !== "number" || senator.yearsInOffice < 0) {
    senator.yearsInOffice = 0;
  }
  if (!senator.initials) {
    senator.initials = senator.name
      .split(/\s+/)
      .map((w) => w[0]?.toUpperCase())
      .slice(0, 2)
      .join("");
  }
  if (!senator.punkNickname) senator.punkNickname = "TBD";

  // Corruption score
  const cs = senator.corruptionScore || {};
  senator.corruptionScore = {
    corporateFunding: clamp(cs.corporateFunding || 0),
    lobbyistAlignment: clamp(cs.lobbyistAlignment || 0),
    industryConcentration: clamp(cs.industryConcentration || 0),
    flipFlopIndex: clamp(cs.flipFlopIndex || 0),
    revolvingDoor: clamp(cs.revolvingDoor || 0),
  };

  // Funding
  const f = senator.funding || {};
  senator.funding = {
    totalRaised: Math.max(0, Math.round(f.totalRaised || 0)),
    totalFromPACs: Math.max(0, Math.round(f.totalFromPACs || 0)),
    smallDonorPercentage: clamp(f.smallDonorPercentage || 0),
    topDonors: (f.topDonors || []).map((d) => ({
      name: d.name || "Unknown",
      total: Math.max(0, Math.round(d.total || 0)),
      type: ["PAC", "Individual", "SuperPAC", "Org/Employees", "Party/Ideological"].includes(d.type)
        ? d.type
        : "PAC",
    })),
    industryBreakdown: (f.industryBreakdown || []).map((ind) => ({
      industry: VALID_INDUSTRIES.has(ind.industry) ? ind.industry : "OTHER",
      name: ind.name || ind.industry || "Other",
      total: Math.max(0, Math.round(ind.total || 0)),
      percentage: clamp(ind.percentage || 0),
    })),
  };

  // Voting record
  const vr = senator.votingRecord || {};
  senator.votingRecord = {
    totalVotes: Math.max(0, vr.totalVotes || 0),
    proCorporateVotes: Math.max(0, vr.proCorporateVotes || 0),
    proConsumerVotes: Math.max(0, vr.proConsumerVotes || 0),
    keyVotes: (vr.keyVotes || []).map((v) => ({
      billName: v.billName || "Unknown Bill",
      billId: v.billId || "",
      date: v.date || "",
      vote: VALID_VOTES.has(v.vote) ? v.vote : "Not Voting",
      proBusinessVote: ["Yea", "Nay"].includes(v.proBusinessVote) ? v.proBusinessVote : null,
      classification: ["pro-corporate", "pro-consumer", "mixed"].includes(v.classification)
        ? v.classification
        : "mixed",
      description: v.description || "",
      corporateInterest: v.corporateInterest || "",
      publicImpact: v.publicImpact || "",
      relevantDonors: Array.isArray(v.relevantDonors) ? v.relevantDonors : [],
      relevantDonorTotal: Math.max(0, Math.round(v.relevantDonorTotal || 0)),
    })),
  };

  // Lobbying matches
  senator.lobbyingMatches = (senator.lobbyingMatches || []).map((m) => ({
    lobbyistOrg: m.lobbyistOrg || "Unknown",
    industry: VALID_INDUSTRIES.has(m.industry) ? m.industry : "OTHER",
    lobbyingSpend: Math.max(0, Math.round(m.lobbyingSpend || 0)),
    donationToSenator: Math.max(0, Math.round(m.donationToSenator || 0)),
    billsInfluenced: Array.isArray(m.billsInfluenced) ? m.billsInfluenced : [],
    senatorVoteAligned: Boolean(m.senatorVoteAligned),
    description: m.description || "",
  }));

  // Remove the bioguideId field (internal, not in Senator type)
  delete senator.bioguideId;

  if (warnings.length > 0) {
    log.warn(`Validation warnings for ${senator.name}: ${warnings.join("; ")}`);
  }

  return senator;
}

function clamp(value, min = 0, max = 100) {
  return Math.max(min, Math.min(max, Math.round(value)));
}
