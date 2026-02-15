import { callClaude } from "./llm-client.mjs";
import { log } from "../config.mjs";

/**
 * Classify ALL bills in a single LLM call to conserve quota.
 * @param {Array} bills - Array of bill objects with text/summary data
 * @returns {Array} Array of classified bills
 */
export async function classifyAllBills(bills) {
  if (bills.length === 0) return [];

  log.info(`Classifying ${bills.length} bills in a single batch...`);

  const billSummaries = bills.map((b) => ({
    billId: b.billId,
    billName: b.billName,
    congress: b.congress,
    summary: (b.summary || "").slice(0, 500),
  }));

  const result = await callClaude({
    promptVersion: "bill-classify-batch-v3",
    systemPrompt: `You are a nonpartisan congressional analyst. Classify bills by their corporate vs. consumer impact. Be factual and balanced. Return ONLY a valid JSON array.`,
    userPrompt: `Classify each of these ${bills.length} bills. For each, determine:
- classification: "pro-corporate", "pro-consumer", or "mixed"
- proBusinessVote: "Yea" or "Nay" — which vote position on this bill serves corporate/business interests. For a bill that increases regulation or taxes on industry, the pro-business vote is "Nay". For a bill that provides subsidies, deregulates, or benefits specific industries, the pro-business vote is "Yea". For mixed bills, choose the vote that more strongly favors corporate interests overall.
- corporateInterest: 1-2 sentences on which industries had a stake
- publicImpact: 1-2 sentences on impact to ordinary people
- description: 1 sentence neutral description
- affectedIndustries: array of codes from [PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER]

Bills:
${JSON.stringify(billSummaries, null, 1)}

Return a JSON array with one object per bill:
[{"billId": "...", "billName": "...", "congress": ..., "date": "", "description": "...", "proBusinessVote": "Yea|Nay", "corporateInterest": "...", "publicImpact": "...", "affectedIndustries": [...], "classification": "..."}]`,
    cacheKey: { billIds: billSummaries.map((b) => b.billId) },
    maxTokens: 8192,
  });

  if (!result || !Array.isArray(result)) {
    log.error("Batch bill classification failed");
    return [];
  }

  // Validate classifications and proBusinessVote
  for (const bill of result) {
    if (!["pro-corporate", "pro-consumer", "mixed"].includes(bill.classification)) {
      bill.classification = "mixed";
    }
    if (!["Yea", "Nay"].includes(bill.proBusinessVote)) {
      bill.proBusinessVote = "Yea";
    }
  }

  log.info(`Classified ${result.length}/${bills.length} bills`);
  return result;
}
