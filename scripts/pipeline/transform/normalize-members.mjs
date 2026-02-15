import { log } from "../config.mjs";

const STATE_NAME_TO_CODE = {
  Alabama: "AL",
  Alaska: "AK",
  Arizona: "AZ",
  Arkansas: "AR",
  California: "CA",
  Colorado: "CO",
  Connecticut: "CT",
  Delaware: "DE",
  Florida: "FL",
  Georgia: "GA",
  Hawaii: "HI",
  Idaho: "ID",
  Illinois: "IL",
  Indiana: "IN",
  Iowa: "IA",
  Kansas: "KS",
  Kentucky: "KY",
  Louisiana: "LA",
  Maine: "ME",
  Maryland: "MD",
  Massachusetts: "MA",
  Michigan: "MI",
  Minnesota: "MN",
  Mississippi: "MS",
  Missouri: "MO",
  Montana: "MT",
  Nebraska: "NE",
  Nevada: "NV",
  "New Hampshire": "NH",
  "New Jersey": "NJ",
  "New Mexico": "NM",
  "New York": "NY",
  "North Carolina": "NC",
  "North Dakota": "ND",
  Ohio: "OH",
  Oklahoma: "OK",
  Oregon: "OR",
  Pennsylvania: "PA",
  "Rhode Island": "RI",
  "South Carolina": "SC",
  "South Dakota": "SD",
  Tennessee: "TN",
  Texas: "TX",
  Utah: "UT",
  Vermont: "VT",
  Virginia: "VA",
  Washington: "WA",
  "West Virginia": "WV",
  Wisconsin: "WI",
  Wyoming: "WY",
};

/**
 * Normalize Congress.gov member data into base senator records.
 * @param {Array} members - Raw member data from Congress.gov API
 * @param {Object} memberDetails - Map of bioguideId -> detailed member info
 * @returns {Array} Array of base senator records
 */
export function normalizeMembers(members, memberDetails = {}) {
  return members
    .filter((m) => {
      // Only current senators — check member detail terms for Senate chamber
      const detail = memberDetails[m.bioguideId] || {};
      const detailTerms = detail?.terms || [];
      const memberTerms = m.terms?.item || [];
      const allTerms = [...detailTerms, ...memberTerms];
      const senateTerm = allTerms.find((t) => t.chamber === "Senate");
      return senateTerm || m.chamber === "Senate";
    })
    .map((m) => {
      const detail = memberDetails[m.bioguideId] || {};
      const name = m.name || `${m.firstName} ${m.lastName}`;
      const state = extractStateCode(m, detail);
      const party = normalizeParty(m.partyName || m.party);
      const yearsInOffice = calculateYearsInOffice(m, detail);

      // Generate ID matching existing format: lastname-firstname
      const nameParts = name.split(/\s+/);
      const lastName = nameParts[nameParts.length - 1].toLowerCase().replace(/[^a-z]/g, "");
      const firstName = nameParts[0].toLowerCase().replace(/[^a-z]/g, "");
      const id = `${lastName}-${firstName}`;

      // Initials
      const initials = nameParts
        .filter((p) => p.length > 0 && p[0] !== "(")
        .map((p) => p[0].toUpperCase())
        .slice(0, 2)
        .join("");

      return {
        bioguideId: m.bioguideId,
        id,
        name: cleanName(name),
        state,
        party,
        yearsInOffice,
        initials,
        // These will be populated later
        punkNickname: "",
        corruptionScore: {
          corporateFunding: 0,
          lobbyistAlignment: 0,
          industryConcentration: 0,
          flipFlopIndex: 0,
          revolvingDoor: 0,
        },
        funding: {
          totalRaised: 0,
          totalFromPACs: 0,
          smallDonorPercentage: 0,
          topDonors: [],
          industryBreakdown: [],
        },
        votingRecord: {
          totalVotes: 0,
          proCorporateVotes: 0,
          proConsumerVotes: 0,
          keyVotes: [],
        },
        lobbyingMatches: [],
      };
    });
}

function cleanName(name) {
  // Congress.gov returns "LastName, FirstName" format sometimes
  if (name.includes(",")) {
    const [last, first] = name.split(",").map((s) => s.trim());
    return `${first} ${last}`;
  }
  // Remove suffixes like Jr., III, etc. from display (keep for ID)
  return name.replace(/\s+(Jr\.|Sr\.|III|IV|II)$/i, (match) => match);
}

function normalizeParty(partyName) {
  if (!partyName) return "I";
  const lower = partyName.toLowerCase();
  if (lower.includes("republican")) return "R";
  if (lower.includes("democrat")) return "D";
  if (lower.includes("independent")) return "I";
  // Single letter check
  if (partyName === "R") return "R";
  if (partyName === "D") return "D";
  return "I";
}

function extractStateCode(member, detail) {
  // First try stateCode from detail terms (most reliable)
  const detailTerms = detail?.terms || [];
  for (const term of detailTerms) {
    if (term.stateCode && term.stateCode.length === 2) return term.stateCode;
  }

  // Try member terms
  const memberTerms = member.terms?.item || [];
  for (const term of memberTerms) {
    if (term.stateCode && term.stateCode.length === 2) return term.stateCode;
  }

  // Convert full state name to code
  const stateName = member.state || detail?.state;
  if (stateName) {
    if (stateName.length === 2) return stateName; // Already a code
    const code = STATE_NAME_TO_CODE[stateName];
    if (code) return code;
  }

  log.warn(`Could not determine state code for ${member.name || member.bioguideId}`);
  return "??";
}

function calculateYearsInOffice(member, detail) {
  // Try to get the earliest Senate start date
  const terms = detail?.terms?.item || member.terms?.item || [];
  const senateTerms = terms.filter((t) => t.chamber === "Senate");

  if (senateTerms.length > 0) {
    // Sort by start year
    const sorted = senateTerms.sort((a, b) => (a.startYear || 9999) - (b.startYear || 9999));
    const firstYear = sorted[0].startYear;
    if (firstYear) {
      return new Date().getFullYear() - firstYear;
    }
  }

  // Fallback: check depiction/service info
  if (member.depiction?.attribution) {
    const match = member.depiction.attribution.match(/since (\d{4})/);
    if (match) return new Date().getFullYear() - parseInt(match[1]);
  }

  log.warn(`Could not determine years in office for ${member.name || member.bioguideId}`);
  return 0;
}
