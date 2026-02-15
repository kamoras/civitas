import { callClaude } from "./llm-client.mjs";
import { log } from "../config.mjs";

/**
 * Run ALL senator-level analysis in a single LLM call:
 * - Cross-reference donors with votes
 * - Generate lobbying matches
 * - Generate flip-flop score
 * - Generate punk nickname
 *
 * This batches everything to conserve API quota.
 *
 * @param {Object} senator - Base senator record
 * @param {Array} donors - Top donors list
 * @param {Array} keyVotes - Classified key votes
 * @returns {Object} { keyVotes, lobbyingMatches, flipFlopScore, punkNickname }
 */
/**
 * Batch-analyze multiple senators in a single LLM call to stay within RPD limits.
 * Each senator's data is included in one big prompt, and the LLM returns an array of results.
 *
 * @param {Array<{senator: Object, donors: Array, keyVotes: Array}>} batch
 * @returns {Array<{senatorId: string, keyVotes: Array, lobbyingMatches: Array, flipFlopScore: number, punkNickname: string}>}
 */
export async function analyzeSenatorBatch(batch) {
  // Separate senators with no data (skip LLM for them)
  const needsAnalysis = batch.filter((b) => b.donors.length > 0 || b.keyVotes.length > 0);
  const noData = batch.filter((b) => b.donors.length === 0 && b.keyVotes.length === 0);

  const results = new Map();

  // Default results for senators with no data
  for (const { senator, keyVotes } of noData) {
    results.set(senator.id, {
      keyVotes,
      lobbyingMatches: [],
      flipFlopScore: 25,
      punkNickname: "TBD",
    });
  }

  if (needsAnalysis.length === 0) {
    return batch.map((b) => ({ senatorId: b.senator.id, ...results.get(b.senator.id) }));
  }

  // Build batch prompt
  const senatorBlocks = needsAnalysis
    .map((b, idx) => {
      const { senator, donors, keyVotes } = b;
      return `--- SENATOR ${idx + 1}: ${senator.name} (${senator.party}-${senator.state}), ${senator.yearsInOffice} yrs ---
ID: ${senator.id}
TOP DONORS (${donors.length}):
${JSON.stringify(
  donors.slice(0, 6).map((d) => ({ name: d.name, total: d.total, type: d.type })),
  null,
  1
)}
KEY VOTES (${keyVotes.length}):
${JSON.stringify(
  keyVotes.slice(0, 8).map((v) => ({
    billId: v.billId,
    billName: v.billName,
    vote: v.vote,
    proBusinessVote: v.proBusinessVote,
    classification: v.classification,
    corporateInterest: v.corporateInterest,
    affectedIndustries: v.affectedIndustries,
  })),
  null,
  1
)}`;
    })
    .join("\n\n");

  const result = await callClaude({
    promptVersion: "senator-batch-analysis-v2",
    systemPrompt: `You are a factual political data analyst. Given multiple senators' donor lists and key votes, provide a comprehensive analysis for EACH senator. Be strictly factual — correlation is not causation. Return ONLY valid JSON.`,
    userPrompt: `Analyze ${needsAnalysis.length} senators. For EACH senator, produce cross-references, lobbying matches, a flip-flop score, and a punk nickname.

${senatorBlocks}

IMPORTANT ANALYSIS RULES:
- Each vote has a "proBusinessVote" field showing which vote direction (Yea/Nay) serves corporate interests on that bill.
- For "senatorVoteAligned": compare the senator's actual "vote" to the "proBusinessVote" field. If they match, the senator voted with corporate interests on that bill.
- Only consider donors with type "PAC" or "Org/Employees" as corporate donors for alignment analysis. Ignore donors with type "Party/Ideological" — they represent broad party funding, not specific corporate interests.
- Focus cross-references on industry-specific donors whose business interests directly relate to the bill's affectedIndustries.

Return a JSON array with one object per senator, in the same order:
[
  {
    "senatorId": "<senator ID from above>",
    "crossReferences": [
      {"billId": "<bill>", "relevantDonors": ["<donor names matching affectedIndustries>"], "relevantDonorTotal": <sum>}
    ],
    "lobbyingMatches": [
      {
        "lobbyistOrg": "<corporate PAC or org from donor list, NOT party PACs>",
        "industry": "<PHARMA|OIL_GAS|FINANCE|DEFENSE|TECH|etc>",
        "lobbyingSpend": <realistic estimate>,
        "donationToSenator": <from donor list>,
        "billsInfluenced": ["<bill IDs>"],
        "senatorVoteAligned": <true if senator vote === proBusinessVote on those bills>,
        "description": "<2-3 factual sentences, no causation claims>"
      }
    ],
    "flipFlopScore": <0-100>,
    "punkNickname": "<2-4 word edgy nickname>"
  }
]

Generate 2-3 lobbying matches per senator from the most notable CORPORATE donor-vote relationships.
Only use donors and bills from the data provided. Do not fabricate.`,
    cacheKey: {
      senatorIds: needsAnalysis.map((b) => b.senator.id),
      donorCounts: needsAnalysis.map((b) => b.donors.length),
      voteCounts: needsAnalysis.map((b) => b.keyVotes.length),
    },
    maxTokens: Math.min(needsAnalysis.length * 3000, 65536),
  });

  if (!result || !Array.isArray(result)) {
    log.warn(`Batch analysis failed for ${needsAnalysis.length} senators`);
    for (const { senator, keyVotes } of needsAnalysis) {
      results.set(senator.id, {
        keyVotes,
        lobbyingMatches: [],
        flipFlopScore: 25,
        punkNickname: "TBD",
      });
    }
    return batch.map((b) => ({ senatorId: b.senator.id, ...results.get(b.senator.id) }));
  }

  // Process each senator's result
  for (let i = 0; i < needsAnalysis.length; i++) {
    const { senator, donors, keyVotes } = needsAnalysis[i];
    // Match by senatorId or by index
    const senatorResult = result.find((r) => r.senatorId === senator.id) || result[i];

    if (!senatorResult) {
      results.set(senator.id, {
        keyVotes,
        lobbyingMatches: [],
        flipFlopScore: 25,
        punkNickname: "TBD",
      });
      continue;
    }

    // Merge cross-references into keyVotes
    const crossRefMap = new Map((senatorResult.crossReferences || []).map((r) => [r.billId, r]));
    const validDonorNames = new Set(donors.map((d) => d.name));

    const updatedVotes = keyVotes.map((vote) => {
      const crossRef = crossRefMap.get(vote.billId);
      if (crossRef) {
        const validRelevant = (crossRef.relevantDonors || []).filter((name) =>
          validDonorNames.has(name)
        );
        const actualTotal = validRelevant.reduce((sum, name) => {
          const donor = donors.find((d) => d.name === name);
          return sum + (donor?.total || 0);
        }, 0);
        return { ...vote, relevantDonors: validRelevant, relevantDonorTotal: actualTotal };
      }
      return vote;
    });

    // Clean lobbying matches
    const lobbyingMatches = (senatorResult.lobbyingMatches || [])
      .filter((m) => m.lobbyistOrg && m.industry)
      .map((m) => ({
        lobbyistOrg: m.lobbyistOrg,
        industry: m.industry,
        lobbyingSpend: Math.round(m.lobbyingSpend || 0),
        donationToSenator: Math.round(m.donationToSenator || 0),
        billsInfluenced: Array.isArray(m.billsInfluenced) ? m.billsInfluenced : [],
        senatorVoteAligned: Boolean(m.senatorVoteAligned),
        description: m.description || "",
      }));

    results.set(senator.id, {
      keyVotes: updatedVotes,
      lobbyingMatches,
      flipFlopScore: Math.max(0, Math.min(100, senatorResult.flipFlopScore || 25)),
      punkNickname: senatorResult.punkNickname || "TBD",
    });
  }

  return batch.map((b) => ({ senatorId: b.senator.id, ...results.get(b.senator.id) }));
}

export async function analyzeSenator(senator, donors, keyVotes) {
  if (!donors.length && !keyVotes.length) {
    return {
      keyVotes,
      lobbyingMatches: [],
      flipFlopScore: 25,
      punkNickname: "TBD",
    };
  }

  const result = await callClaude({
    promptVersion: "senator-analysis-v2",
    systemPrompt: `You are a factual political data analyst. Given a senator's donor list, key votes, and bill classifications, provide a comprehensive analysis. Be strictly factual — correlation is not causation. Return ONLY valid JSON.`,
    userPrompt: `Analyze Senator ${senator.name} (${senator.party}-${senator.state}), ${senator.yearsInOffice} years in office.

TOP DONORS (${donors.length}):
${JSON.stringify(
  donors.slice(0, 8).map((d) => ({ name: d.name, total: d.total, type: d.type })),
  null,
  1
)}

KEY VOTES (${keyVotes.length}):
${JSON.stringify(
  keyVotes.slice(0, 12).map((v) => ({
    billId: v.billId,
    billName: v.billName,
    vote: v.vote,
    corporateInterest: v.corporateInterest,
    affectedIndustries: v.affectedIndustries,
  })),
  null,
  1
)}

Return a single JSON object with ALL of these sections:

{
  "crossReferences": [
    {"billId": "<bill>", "relevantDonors": ["<donor names from list>"], "relevantDonorTotal": <sum>}
  ],
  "lobbyingMatches": [
    {
      "lobbyistOrg": "<org from donor list>",
      "industry": "<PHARMA|OIL_GAS|FINANCE|DEFENSE|TECH|etc>",
      "lobbyingSpend": <realistic estimate>,
      "donationToSenator": <from donor list>,
      "billsInfluenced": ["<bill IDs>"],
      "senatorVoteAligned": <true/false>,
      "description": "<2-3 factual sentences, no causation claims>"
    }
  ],
  "flipFlopScore": <0-100, 0=consistent, 100=inconsistent>,
  "punkNickname": "<2-4 word edgy nickname based on their top industry/donors>"
}

Generate 2-4 lobbying matches from the most notable donor-vote relationships.
Only use donors and bills from the data provided. Do not fabricate.`,
    cacheKey: {
      senatorId: senator.id,
      donorCount: donors.length,
      voteCount: keyVotes.length,
    },
    maxTokens: 4096,
  });

  if (!result) {
    log.warn(`Full analysis failed for ${senator.name}`);
    return {
      keyVotes,
      lobbyingMatches: [],
      flipFlopScore: 25,
      punkNickname: "TBD",
    };
  }

  // Merge cross-references into keyVotes
  const crossRefMap = new Map((result.crossReferences || []).map((r) => [r.billId, r]));
  const validDonorNames = new Set(donors.map((d) => d.name));

  const updatedVotes = keyVotes.map((vote) => {
    const crossRef = crossRefMap.get(vote.billId);
    if (crossRef) {
      const validRelevant = (crossRef.relevantDonors || []).filter((name) =>
        validDonorNames.has(name)
      );
      const actualTotal = validRelevant.reduce((sum, name) => {
        const donor = donors.find((d) => d.name === name);
        return sum + (donor?.total || 0);
      }, 0);
      return { ...vote, relevantDonors: validRelevant, relevantDonorTotal: actualTotal };
    }
    return vote;
  });

  // Clean lobbying matches
  const lobbyingMatches = (result.lobbyingMatches || [])
    .filter((m) => m.lobbyistOrg && m.industry)
    .map((m) => ({
      lobbyistOrg: m.lobbyistOrg,
      industry: m.industry,
      lobbyingSpend: Math.round(m.lobbyingSpend || 0),
      donationToSenator: Math.round(m.donationToSenator || 0),
      billsInfluenced: Array.isArray(m.billsInfluenced) ? m.billsInfluenced : [],
      senatorVoteAligned: Boolean(m.senatorVoteAligned),
      description: m.description || "",
    }));

  return {
    keyVotes: updatedVotes,
    lobbyingMatches,
    flipFlopScore: Math.max(0, Math.min(100, result.flipFlopScore || 25)),
    punkNickname: result.punkNickname || "TBD",
  };
}
