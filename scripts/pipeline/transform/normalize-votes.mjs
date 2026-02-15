import { log } from "../config.mjs";

/**
 * Normalize voting data for a senator.
 * Combines bill classification data with the senator's actual votes.
 *
 * @param {string} bioguideId - Senator's Bioguide ID
 * @param {Array} billClassifications - LLM-classified bills with vote data
 * @param {Object} senatorVotes - Map of billId -> senator's vote on that bill
 * @returns {Object} Normalized voting record matching Senator.votingRecord type
 */
export function normalizeVotes(bioguideId, billClassifications, senatorVotes) {
  const keyVotes = [];
  let proCorporateVotes = 0;
  let proConsumerVotes = 0;
  let totalTracked = 0;

  for (const bill of billClassifications) {
    const vote = senatorVotes[bill.billId];
    if (!vote) continue; // Senator didn't vote on this bill

    totalTracked++;

    // Normalize vote value
    const voteDirection = vote.toUpperCase();
    const isYea = voteDirection === "YEA" || voteDirection === "AYE" || voteDirection === "YES";
    const isNay = voteDirection === "NAY" || voteDirection === "NO";

    let normalizedVote = "Not Voting";
    if (isYea) normalizedVote = "Yea";
    else if (isNay) normalizedVote = "Nay";

    // Determine alignment using the LLM-provided proBusinessVote field
    // This tells us which vote direction (Yea/Nay) serves corporate interests
    if (bill.proBusinessVote && normalizedVote !== "Not Voting") {
      const votedProBusiness =
        (normalizedVote === "Yea" && bill.proBusinessVote === "Yea") ||
        (normalizedVote === "Nay" && bill.proBusinessVote === "Nay");

      if (votedProBusiness) {
        proCorporateVotes++;
      } else {
        proConsumerVotes++;
      }
    }
    // Bills without proBusinessVote or "Not Voting" — don't count toward either side

    keyVotes.push({
      billName: bill.billName,
      billId: bill.billId,
      date: bill.date || "",
      vote: normalizedVote,
      proBusinessVote: bill.proBusinessVote || null,
      classification: bill.classification || "mixed",
      description: bill.description || "",
      corporateInterest: bill.corporateInterest || "",
      publicImpact: bill.publicImpact || "",
      relevantDonors: [], // Populated by cross-reference.mjs
      relevantDonorTotal: 0,
    });
  }

  // Estimate total votes from tracked sample
  // Real senators vote on hundreds of bills; our tracked set is a curated sample
  const estimatedTotal = totalTracked > 0 ? Math.round(totalTracked * 15) : 300;

  return {
    totalVotes: estimatedTotal,
    proCorporateVotes: Math.round((proCorporateVotes / Math.max(totalTracked, 1)) * estimatedTotal),
    proConsumerVotes: Math.round((proConsumerVotes / Math.max(totalTracked, 1)) * estimatedTotal),
    keyVotes,
  };
}

/**
 * Extract a senator's vote from roll call vote data.
 * Matches by last name + state since senate.gov XML doesn't include bioguideId.
 * @param {Object} rollCallData - Parsed roll call vote data from senate.gov
 * @param {string} bioguideId - Senator's Bioguide ID (unused, kept for signature compat)
 * @param {string} [lastName] - Senator's last name for matching
 * @param {string} [state] - Senator's state code for matching
 * @returns {string|null} Vote position ("Yea", "Nay", "Not Voting") or null
 */
export function extractSenatorVote(rollCallData, bioguideId, lastName, state) {
  if (!rollCallData?.members) return null;

  // Match by last name + state
  const member = rollCallData.members.find((m) => {
    if (lastName && state) {
      return (
        m.lastName.toUpperCase() === lastName.toUpperCase() &&
        m.state.toUpperCase() === state.toUpperCase()
      );
    }
    return false;
  });

  if (!member) return null;
  return member.voteCast || null;
}

/**
 * Try to find the Senate roll call vote for a bill from its actions.
 * @param {Array} actions - Bill actions from Congress.gov
 * @returns {Object|null} { congress, session, rollCallNumber } or null
 */
export function findSenateRollCall(actions) {
  if (!actions) return null;

  for (const action of actions) {
    // Look for Senate roll call vote actions
    const text = (action.text || "").toLowerCase();
    if (
      (text.includes("passed senate") ||
        text.includes("senate agreed") ||
        text.includes("cloture") ||
        text.includes("roll call vote")) &&
      action.recordedVotes
    ) {
      for (const rv of action.recordedVotes) {
        if (rv.chamber === "Senate" && rv.rollNumber) {
          return {
            congress: rv.congress,
            session: rv.sessionNumber,
            rollCallNumber: rv.rollNumber,
          };
        }
      }
    }
  }

  return null;
}
