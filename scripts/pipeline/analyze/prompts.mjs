/**
 * All LLM prompt templates for the pipeline.
 * Each function returns { systemPrompt, userPrompt, promptVersion }.
 */

export function billClassificationPrompt(bill) {
  return {
    promptVersion: "bill-classify-v1",
    systemPrompt: `You are a nonpartisan congressional analyst. Classify bills by their corporate vs. consumer impact. Be factual and balanced. Return ONLY valid JSON with no additional text.`,
    userPrompt: `Analyze this bill and return a JSON object:

Bill: ${bill.billId} - ${bill.billName}
Congress: ${bill.congress}
${bill.summary ? `Summary: ${bill.summary}` : ""}
${bill.fullText ? `Full text (excerpt): ${bill.fullText.slice(0, 4000)}` : ""}

Return this exact JSON structure:
{
  "billId": "${bill.billId}",
  "billName": "${bill.billName}",
  "congress": ${bill.congress},
  "date": "<date of final Senate vote if known, or empty string>",
  "description": "<1-2 sentence neutral description of what the bill does>",
  "corporateInterest": "<1-2 sentences: which industries had a stake and why>",
  "publicImpact": "<1-2 sentences: concrete impact on ordinary people>",
  "affectedIndustries": ["<IndustryCode values from: PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER>"],
  "classification": "<one of: pro-corporate, pro-consumer, mixed>"
}`,
  };
}

export function crossReferencePrompt(senator, donors, keyVotes) {
  return {
    promptVersion: "cross-ref-v1",
    systemPrompt: `You are a factual campaign finance analyst. Given a senator's donor list and their votes on key bills, identify connections between donors and relevant votes. Be strictly factual — correlation is not causation. Only identify connections where a donor's industry directly relates to a bill's subject matter. Return ONLY valid JSON.`,
    userPrompt: `Senator: ${senator.name} (${senator.party}-${senator.state})

Top donors:
${JSON.stringify(donors, null, 2)}

Key votes:
${JSON.stringify(
  keyVotes.map((v) => ({
    billId: v.billId,
    billName: v.billName,
    vote: v.vote,
    corporateInterest: v.corporateInterest,
    affectedIndustries: v.affectedIndustries,
  })),
  null,
  2
)}

For each key vote, identify which donors (if any) have a direct industry connection to the bill. Return a JSON array:
[
  {
    "billId": "<bill ID>",
    "relevantDonors": ["<donor names from the list above that are in affected industries>"],
    "relevantDonorTotal": <sum of those donors' contributions>,
    "explanation": "<1 factual sentence about the connection, if any>"
  }
]

Only include donors whose industry directly relates to the bill. If no donors are relevant to a bill, use empty arrays and 0. Do not invent connections.`,
  };
}

export function lobbyingMatchPrompt(senator, donors, keyVotes) {
  return {
    promptVersion: "lobbying-match-v1",
    systemPrompt: `You are a factual lobbying analyst. Given a senator's top donors and their votes on key bills, generate lobbying match records that show the relationship between donations and legislative activity. Be neutral and factual. Correlation does not imply causation. Return ONLY valid JSON.`,
    userPrompt: `Senator: ${senator.name} (${senator.party}-${senator.state})

Top donors:
${JSON.stringify(donors.slice(0, 8), null, 2)}

Key votes:
${JSON.stringify(
  keyVotes.slice(0, 10).map((v) => ({
    billId: v.billId,
    billName: v.billName,
    vote: v.vote,
    corporateInterest: v.corporateInterest,
  })),
  null,
  2
)}

Generate 2-4 lobbying match records for the most notable donor-vote relationships. Return a JSON array:
[
  {
    "lobbyistOrg": "<donor/org name from the donor list>",
    "industry": "<IndustryCode: PHARMA, OIL_GAS, FINANCE, DEFENSE, TECH, etc.>",
    "lobbyingSpend": <estimated lobbying spend based on org size — use realistic figures>,
    "donationToSenator": <actual donation amount from the donor list>,
    "billsInfluenced": ["<bill IDs from the key votes list>"],
    "senatorVoteAligned": <true if senator voted in the direction the org would prefer, false otherwise>,
    "description": "<2-3 factual sentences describing the org's lobbying interest, donation, and the senator's vote. Do not imply causation.>"
  }
]

Only use donors and bills from the data provided. Do not fabricate organizations or amounts.`,
  };
}

export function flipFlopPrompt(senator, keyVotes) {
  return {
    promptVersion: "flipflop-v1",
    systemPrompt: `You analyze legislative consistency. A "flip-flop" means voting differently on substantially similar legislation across sessions, or publicly advocating one position while voting the opposite way. Be fair — changing one's mind based on new evidence is not necessarily a flip-flop. Return ONLY valid JSON.`,
    userPrompt: `Senator: ${senator.name} (${senator.party}-${senator.state}), ${senator.yearsInOffice} years in office

Voting record on key bills:
${JSON.stringify(
  keyVotes.map((v) => ({
    billId: v.billId,
    billName: v.billName,
    vote: v.vote,
    description: v.description,
  })),
  null,
  2
)}

Analyze for consistency and return:
{
  "flipFlopScore": <0-100, where 0 is perfectly consistent and 100 is completely inconsistent>,
  "examples": ["<brief factual description of any inconsistencies found>"],
  "reasoning": "<1-2 sentences explaining the score>"
}

If there are no clear inconsistencies, return a low score with an empty examples array.`,
  };
}

export function nicknamePrompt(senator) {
  return {
    promptVersion: "nickname-v1",
    systemPrompt: `You are a punk zine editor creating factual but edgy nicknames for politicians. Nicknames should be 2-4 words, reference the senator's actual data (top industry, notable votes, years in office), and be irreverent but not libelous. Think punk rock, not defamation.`,
    userPrompt: `Generate a punk nickname for this senator:

Name: ${senator.name} (${senator.party}-${senator.state})
Years in office: ${senator.yearsInOffice}
Top funding industry: ${senator.funding?.industryBreakdown?.[0]?.name || "Unknown"}
PAC funding: ${senator.funding?.totalFromPACs ? `$${(senator.funding.totalFromPACs / 1000000).toFixed(1)}M` : "Unknown"}
Small donor %: ${senator.funding?.smallDonorPercentage || 0}%
Corporate influence score: ${senator.corruptionScore?.corporateFunding || 0}/100

Return: { "punkNickname": "<2-4 word nickname>" }`,
  };
}

export function industryClassificationPrompt(orgNames) {
  return {
    promptVersion: "industry-classify-v1",
    systemPrompt: `Classify each organization into exactly one industry code. Be accurate. If uncertain, use OTHER. Return ONLY valid JSON.`,
    userPrompt: `Classify these organizations into industry codes.

Valid codes: PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER

Organizations:
${orgNames.map((n, i) => `${i + 1}. "${n}"`).join("\n")}

Return a JSON array:
[{"name": "<org name>", "industry": "<IndustryCode>"}]`,
  };
}
