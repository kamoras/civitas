#!/usr/bin/env node
// Generates the senators.json dataset
import { writeFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Real 2024-2025 senators by state
const SENATOR_DATA = [
  // Alabama
  {
    name: "Tommy Tuberville",
    state: "AL",
    party: "R",
    years: 4,
    nick: "Coach Corruption",
    initials: "TT",
  },
  {
    name: "Katie Britt",
    state: "AL",
    party: "R",
    years: 2,
    nick: "Britt the Brand",
    initials: "KB",
  },
  // Alaska
  {
    name: "Lisa Murkowski",
    state: "AK",
    party: "R",
    years: 22,
    nick: "The Survivor",
    initials: "LM",
  },
  {
    name: "Dan Sullivan",
    state: "AK",
    party: "R",
    years: 10,
    nick: "Pipeline Dan",
    initials: "DS",
  },
  // Arizona
  {
    name: "Kyrsten Sinema",
    state: "AZ",
    party: "I",
    years: 6,
    nick: "Senator Side-Switch",
    initials: "KS",
  },
  { name: "Ruben Gallego", state: "AZ", party: "D", years: 1, nick: "The New Guy", initials: "RG" },
  // Arkansas
  {
    name: "John Boozman",
    state: "AR",
    party: "R",
    years: 14,
    nick: "Boozman the Quiet",
    initials: "JB",
  },
  {
    name: "Tom Cotton",
    state: "AR",
    party: "R",
    years: 10,
    nick: "Cotton-Eyed Hawk",
    initials: "TC",
  },
  // California
  {
    name: "Alex Padilla",
    state: "CA",
    party: "D",
    years: 4,
    nick: "Padilla the Placeholder",
    initials: "AP",
  },
  { name: "Adam Schiff", state: "CA", party: "D", years: 1, nick: "Schiff Show", initials: "AS" },
  // Colorado
  {
    name: "Michael Bennet",
    state: "CO",
    party: "D",
    years: 16,
    nick: "Bennet the Bland",
    initials: "MB",
  },
  {
    name: "John Hickenlooper",
    state: "CO",
    party: "D",
    years: 4,
    nick: "Frackenlooper",
    initials: "JH",
  },
  // Connecticut
  {
    name: "Richard Blumenthal",
    state: "CT",
    party: "D",
    years: 14,
    nick: "Blumenthal the Bloviator",
    initials: "RB",
  },
  {
    name: "Chris Murphy",
    state: "CT",
    party: "D",
    years: 12,
    nick: "Murphy's Law",
    initials: "CM",
  },
  // Delaware
  {
    name: "Tom Carper",
    state: "DE",
    party: "D",
    years: 24,
    nick: "Corporate Carper",
    initials: "TC",
  },
  {
    name: "Chris Coons",
    state: "DE",
    party: "D",
    years: 14,
    nick: "Coons the Centrist",
    initials: "CC",
  },
  // Florida
  { name: "Marco Rubio", state: "FL", party: "R", years: 14, nick: "Little Marco", initials: "MR" },
  {
    name: "Rick Scott",
    state: "FL",
    party: "R",
    years: 6,
    nick: "Medicare Fraud Rick",
    initials: "RS",
  },
  // Georgia
  {
    name: "Jon Ossoff",
    state: "GA",
    party: "D",
    years: 4,
    nick: "Wall Street Jon",
    initials: "JO",
  },
  {
    name: "Raphael Warnock",
    state: "GA",
    party: "D",
    years: 4,
    nick: "Reverend Revenue",
    initials: "RW",
  },
  // Hawaii
  {
    name: "Mazie Hirono",
    state: "HI",
    party: "D",
    years: 12,
    nick: "Mazie the Steady",
    initials: "MH",
  },
  {
    name: "Brian Schatz",
    state: "HI",
    party: "D",
    years: 12,
    nick: "Schatz Happens",
    initials: "BS",
  },
  // Idaho
  {
    name: "Mike Crapo",
    state: "ID",
    party: "R",
    years: 26,
    nick: "Crapo the Crypto Bro",
    initials: "MC",
  },
  { name: "Jim Risch", state: "ID", party: "R", years: 16, nick: "Risch Business", initials: "JR" },
  // Illinois
  {
    name: "Dick Durbin",
    state: "IL",
    party: "D",
    years: 28,
    nick: "Durbin the Dinosaur",
    initials: "DD",
  },
  {
    name: "Tammy Duckworth",
    state: "IL",
    party: "D",
    years: 8,
    nick: "Duckworth the Decorated",
    initials: "TD",
  },
  // Indiana
  {
    name: "Todd Young",
    state: "IN",
    party: "R",
    years: 8,
    nick: "Young Money Todd",
    initials: "TY",
  },
  {
    name: "Jim Banks",
    state: "IN",
    party: "R",
    years: 1,
    nick: "Banks the Banker",
    initials: "JB",
  },
  // Iowa
  {
    name: "Chuck Grassley",
    state: "IA",
    party: "R",
    years: 44,
    nick: "Grassley the Fossil",
    initials: "CG",
  },
  {
    name: "Joni Ernst",
    state: "IA",
    party: "R",
    years: 10,
    nick: "Joni Makes 'Em Squeal",
    initials: "JE",
  },
  // Kansas
  {
    name: "Jerry Moran",
    state: "KS",
    party: "R",
    years: 14,
    nick: "Koch's Kansas Man",
    initials: "JM",
  },
  {
    name: "Roger Marshall",
    state: "KS",
    party: "R",
    years: 4,
    nick: "Dr. Dark Money",
    initials: "RM",
  },
  // Kentucky
  {
    name: "Mitch McConnell",
    state: "KY",
    party: "R",
    years: 40,
    nick: "Moscow Mitch",
    initials: "MM",
  },
  {
    name: "Rand Paul",
    state: "KY",
    party: "R",
    years: 14,
    nick: "Dr. No-Oversight",
    initials: "RP",
  },
  // Louisiana
  {
    name: "Bill Cassidy",
    state: "LA",
    party: "R",
    years: 10,
    nick: "Cassidy the Compliant",
    initials: "BC",
  },
  {
    name: "John Kennedy",
    state: "LA",
    party: "R",
    years: 8,
    nick: "Not THAT Kennedy",
    initials: "JK",
  },
  // Maine
  {
    name: "Susan Collins",
    state: "ME",
    party: "R",
    years: 28,
    nick: "Deeply Concerned Collins",
    initials: "SC",
  },
  {
    name: "Angus King",
    state: "ME",
    party: "I",
    years: 12,
    nick: "King of the Middle",
    initials: "AK",
  },
  // Maryland
  {
    name: "Chris Van Hollen",
    state: "MD",
    party: "D",
    years: 8,
    nick: "Van Hollen the Hollow",
    initials: "CV",
  },
  {
    name: "Angela Alsobrooks",
    state: "MD",
    party: "D",
    years: 1,
    nick: "The New Establishment",
    initials: "AA",
  },
  // Massachusetts
  {
    name: "Elizabeth Warren",
    state: "MA",
    party: "D",
    years: 12,
    nick: "Wall Street's Nemesis",
    initials: "EW",
  },
  {
    name: "Ed Markey",
    state: "MA",
    party: "D",
    years: 10,
    nick: "Markey the Lifer",
    initials: "EM",
  },
  // Michigan
  {
    name: "Gary Peters",
    state: "MI",
    party: "D",
    years: 10,
    nick: "Invisible Gary",
    initials: "GP",
  },
  {
    name: "Elissa Slotkin",
    state: "MI",
    party: "D",
    years: 1,
    nick: "Slotkin the Spook",
    initials: "ES",
  },
  // Minnesota
  {
    name: "Amy Klobuchar",
    state: "MN",
    party: "D",
    years: 18,
    nick: "Klobuchar the Corporate Dem",
    initials: "AK",
  },
  {
    name: "Tina Smith",
    state: "MN",
    party: "D",
    years: 7,
    nick: "Tina from Target",
    initials: "TS",
  },
  // Mississippi
  {
    name: "Roger Wicker",
    state: "MS",
    party: "R",
    years: 18,
    nick: "Wicker the Weapons Dealer",
    initials: "RW",
  },
  {
    name: "Cindy Hyde-Smith",
    state: "MS",
    party: "R",
    years: 6,
    nick: "Hyde and Don't Seek",
    initials: "CH",
  },
  // Missouri
  {
    name: "Josh Hawley",
    state: "MO",
    party: "R",
    years: 6,
    nick: "Fist Pump Josh",
    initials: "JH",
  },
  {
    name: "Eric Schmitt",
    state: "MO",
    party: "R",
    years: 2,
    nick: "Schmitt the Suer",
    initials: "ES",
  },
  // Montana
  {
    name: "Steve Daines",
    state: "MT",
    party: "R",
    years: 10,
    nick: "Daines the Developer",
    initials: "SD",
  },
  {
    name: "Tim Sheehy",
    state: "MT",
    party: "R",
    years: 1,
    nick: "Sheehy the Shady",
    initials: "TS",
  },
  // Nebraska
  {
    name: "Deb Fischer",
    state: "NE",
    party: "R",
    years: 12,
    nick: "Fischer Price Senator",
    initials: "DF",
  },
  {
    name: "Pete Ricketts",
    state: "NE",
    party: "R",
    years: 2,
    nick: "Ricketts the Rich Kid",
    initials: "PR",
  },
  // Nevada
  {
    name: "Catherine Cortez Masto",
    state: "NV",
    party: "D",
    years: 8,
    nick: "Casino Cortez",
    initials: "CC",
  },
  {
    name: "Jacky Rosen",
    state: "NV",
    party: "D",
    years: 6,
    nick: "Rosen the Gambler",
    initials: "JR",
  },
  // New Hampshire
  {
    name: "Jeanne Shaheen",
    state: "NH",
    party: "D",
    years: 16,
    nick: "Shaheen the Machine",
    initials: "JS",
  },
  {
    name: "Maggie Hassan",
    state: "NH",
    party: "D",
    years: 8,
    nick: "Hassan the Hedge",
    initials: "MH",
  },
  // New Jersey
  {
    name: "Cory Booker",
    state: "NJ",
    party: "D",
    years: 12,
    nick: "Big Pharma Booker",
    initials: "CB",
  },
  { name: "Andy Kim", state: "NJ", party: "D", years: 1, nick: "Kim the Clean", initials: "AK" },
  // New Mexico
  {
    name: "Martin Heinrich",
    state: "NM",
    party: "D",
    years: 12,
    nick: "Heinrich the Hawk",
    initials: "MH",
  },
  {
    name: "Ben Ray Lujan",
    state: "NM",
    party: "D",
    years: 4,
    nick: "Lujan the Loyal",
    initials: "BL",
  },
  // New York
  {
    name: "Chuck Schumer",
    state: "NY",
    party: "D",
    years: 26,
    nick: "Wall Street Chuck",
    initials: "CS",
  },
  {
    name: "Kirsten Gillibrand",
    state: "NY",
    party: "D",
    years: 16,
    nick: "Gillibrand the Weathervane",
    initials: "KG",
  },
  // North Carolina
  {
    name: "Thom Tillis",
    state: "NC",
    party: "R",
    years: 10,
    nick: "Tillis the Tool",
    initials: "TT",
  },
  { name: "Ted Budd", state: "NC", party: "R", years: 2, nick: "Budd Light", initials: "TB" },
  // North Dakota
  {
    name: "John Hoeven",
    state: "ND",
    party: "R",
    years: 14,
    nick: "Hoeven the Oil Baron",
    initials: "JH",
  },
  {
    name: "Kevin Cramer",
    state: "ND",
    party: "R",
    years: 6,
    nick: "Cramer the Crude",
    initials: "KC",
  },
  // Ohio
  {
    name: "Sherrod Brown",
    state: "OH",
    party: "D",
    years: 18,
    nick: "Sherrod the Worker",
    initials: "SB",
  },
  {
    name: "Bernie Moreno",
    state: "OH",
    party: "R",
    years: 1,
    nick: "Moreno the Dealership",
    initials: "BM",
  },
  // Oklahoma
  {
    name: "James Lankford",
    state: "OK",
    party: "R",
    years: 10,
    nick: "Lankford the Lapdog",
    initials: "JL",
  },
  {
    name: "Markwayne Mullin",
    state: "OK",
    party: "R",
    years: 2,
    nick: "Mullin the Millionaire",
    initials: "MM",
  },
  // Oregon
  {
    name: "Ron Wyden",
    state: "OR",
    party: "D",
    years: 30,
    nick: "Wyden the Watchdog",
    initials: "RW",
  },
  {
    name: "Jeff Merkley",
    state: "OR",
    party: "D",
    years: 16,
    nick: "Merkley the Progressive",
    initials: "JM",
  },
  // Pennsylvania
  {
    name: "Bob Casey",
    state: "PA",
    party: "D",
    years: 18,
    nick: "Casey the Dynasty",
    initials: "BC",
  },
  {
    name: "John Fetterman",
    state: "PA",
    party: "D",
    years: 2,
    nick: "Big Fett Energy",
    initials: "JF",
  },
  // Rhode Island
  {
    name: "Jack Reed",
    state: "RI",
    party: "D",
    years: 28,
    nick: "Reed the Defense Contractor",
    initials: "JR",
  },
  {
    name: "Sheldon Whitehouse",
    state: "RI",
    party: "D",
    years: 18,
    nick: "Whitehouse the Wealthy",
    initials: "SW",
  },
  // South Carolina
  {
    name: "Lindsey Graham",
    state: "SC",
    party: "R",
    years: 22,
    nick: "Lady Lindsey",
    initials: "LG",
  },
  {
    name: "Tim Scott",
    state: "SC",
    party: "R",
    years: 12,
    nick: "Wall Street Tim",
    initials: "TS",
  },
  // South Dakota
  {
    name: "John Thune",
    state: "SD",
    party: "R",
    years: 20,
    nick: "Thune the Telecom",
    initials: "JT",
  },
  {
    name: "Mike Rounds",
    state: "SD",
    party: "R",
    years: 10,
    nick: "Rounds of Ammo",
    initials: "MR",
  },
  // Tennessee
  {
    name: "Marsha Blackburn",
    state: "TN",
    party: "R",
    years: 6,
    nick: "Blackburn the Broadband Blocker",
    initials: "MB",
  },
  {
    name: "Bill Hagerty",
    state: "TN",
    party: "R",
    years: 4,
    nick: "Hagerty the Hedge Fund",
    initials: "BH",
  },
  // Texas
  { name: "Ted Cruz", state: "TX", party: "R", years: 12, nick: "Cancun Cruz", initials: "TC" },
  {
    name: "John Cornyn",
    state: "TX",
    party: "R",
    years: 22,
    nick: "Cornyn the Corporate",
    initials: "JC",
  },
  // Utah
  {
    name: "Mike Lee",
    state: "UT",
    party: "R",
    years: 14,
    nick: "Lee the Libertarian Grifter",
    initials: "ML",
  },
  {
    name: "John Curtis",
    state: "UT",
    party: "R",
    years: 1,
    nick: "Curtis the Clean Coal",
    initials: "JC",
  },
  // Vermont
  {
    name: "Bernie Sanders",
    state: "VT",
    party: "I",
    years: 18,
    nick: "The People's Senator",
    initials: "BS",
  },
  {
    name: "Peter Welch",
    state: "VT",
    party: "D",
    years: 2,
    nick: "Welch the Worker",
    initials: "PW",
  },
  // Virginia
  {
    name: "Mark Warner",
    state: "VA",
    party: "D",
    years: 16,
    nick: "Warner the Wall Streeter",
    initials: "MW",
  },
  {
    name: "Tim Kaine",
    state: "VA",
    party: "D",
    years: 12,
    nick: "Kaine the Cautious",
    initials: "TK",
  },
  // Washington
  {
    name: "Patty Murray",
    state: "WA",
    party: "D",
    years: 32,
    nick: "Murray the Machine",
    initials: "PM",
  },
  {
    name: "Maria Cantwell",
    state: "WA",
    party: "D",
    years: 24,
    nick: "Cantwell the Tech Enabler",
    initials: "MC",
  },
  // West Virginia
  {
    name: "Shelley Moore Capito",
    state: "WV",
    party: "R",
    years: 10,
    nick: "Capito the Coal Queen",
    initials: "SC",
  },
  {
    name: "Jim Justice",
    state: "WV",
    party: "R",
    years: 1,
    nick: "Justice for Sale",
    initials: "JJ",
  },
  // Wisconsin
  { name: "Ron Johnson", state: "WI", party: "R", years: 14, nick: "Russian Ron", initials: "RJ" },
  {
    name: "Tammy Baldwin",
    state: "WI",
    party: "D",
    years: 12,
    nick: "Baldwin the Balanced",
    initials: "TB",
  },
  // Wyoming
  {
    name: "John Barrasso",
    state: "WY",
    party: "R",
    years: 18,
    nick: "Barrasso the Oil Baron",
    initials: "JB",
  },
  {
    name: "Cynthia Lummis",
    state: "WY",
    party: "R",
    years: 4,
    nick: "Crypto Cynthia",
    initials: "CL",
  },
];

// Top donor organizations by industry
const DONORS_BY_INDUSTRY = {
  PHARMA: [
    { name: "Pfizer Inc", type: "PAC" },
    { name: "PhRMA", type: "PAC" },
    { name: "AbbVie Inc", type: "PAC" },
    { name: "Johnson & Johnson", type: "PAC" },
    { name: "Merck & Co", type: "PAC" },
    { name: "Amgen Inc", type: "PAC" },
  ],
  OIL_GAS: [
    { name: "Koch Industries", type: "PAC" },
    { name: "ExxonMobil", type: "PAC" },
    { name: "Chevron Corp", type: "PAC" },
    { name: "ConocoPhillips", type: "PAC" },
    { name: "Marathon Petroleum", type: "PAC" },
    { name: "Devon Energy", type: "Individual" },
  ],
  FINANCE: [
    { name: "Goldman Sachs", type: "Individual" },
    { name: "JPMorgan Chase", type: "PAC" },
    { name: "Blackstone Group", type: "Individual" },
    { name: "Citigroup Inc", type: "PAC" },
    { name: "Morgan Stanley", type: "Individual" },
    { name: "Bank of America", type: "PAC" },
  ],
  DEFENSE: [
    { name: "Lockheed Martin", type: "PAC" },
    { name: "Raytheon Technologies", type: "PAC" },
    { name: "Northrop Grumman", type: "PAC" },
    { name: "General Dynamics", type: "PAC" },
    { name: "Boeing Co", type: "PAC" },
    { name: "L3Harris Technologies", type: "PAC" },
  ],
  TECH: [
    { name: "Alphabet Inc", type: "Individual" },
    { name: "Microsoft Corp", type: "PAC" },
    { name: "Meta Platforms", type: "Individual" },
    { name: "Amazon.com", type: "Individual" },
    { name: "Apple Inc", type: "Individual" },
    { name: "Oracle Corp", type: "PAC" },
  ],
  INSURANCE: [
    { name: "Blue Cross/Blue Shield", type: "PAC" },
    { name: "UnitedHealth Group", type: "PAC" },
    { name: "AHIP", type: "PAC" },
    { name: "Cigna Group", type: "PAC" },
    { name: "Humana Inc", type: "PAC" },
  ],
  REAL_ESTATE: [
    { name: "National Assn of Realtors", type: "PAC" },
    { name: "Blackstone Real Estate", type: "Individual" },
    { name: "CBRE Group", type: "PAC" },
    { name: "Brookfield Asset Mgmt", type: "Individual" },
  ],
  TELECOM: [
    { name: "AT&T Inc", type: "PAC" },
    { name: "Comcast Corp", type: "PAC" },
    { name: "Verizon Communications", type: "PAC" },
    { name: "T-Mobile US", type: "PAC" },
  ],
  LAWYERS: [
    { name: "DLA Piper", type: "Individual" },
    { name: "Akin Gump et al", type: "Individual" },
    { name: "Kirkland & Ellis", type: "Individual" },
    { name: "Skadden Arps et al", type: "Individual" },
  ],
  AGRIBUSINESS: [
    { name: "American Farm Bureau", type: "PAC" },
    { name: "Cargill Inc", type: "PAC" },
    { name: "Deere & Co", type: "PAC" },
    { name: "Monsanto/Bayer", type: "PAC" },
  ],
  ENERGY: [
    { name: "NextEra Energy", type: "PAC" },
    { name: "Southern Company", type: "PAC" },
    { name: "Duke Energy", type: "PAC" },
    { name: "Dominion Energy", type: "PAC" },
  ],
  GUNS: [
    { name: "National Rifle Assn", type: "PAC" },
    { name: "Gun Owners of America", type: "PAC" },
    { name: "Safari Club International", type: "PAC" },
  ],
  CRYPTO: [
    { name: "Coinbase Global", type: "Individual" },
    { name: "Fairshake PAC", type: "SuperPAC" },
    { name: "Andreessen Horowitz", type: "Individual" },
  ],
  TOBACCO: [
    { name: "Altria Group", type: "PAC" },
    { name: "Reynolds American", type: "PAC" },
    { name: "Philip Morris Intl", type: "PAC" },
  ],
  PRIVATE_PRISON: [
    { name: "GEO Group", type: "PAC" },
    { name: "CoreCivic Inc", type: "PAC" },
  ],
  GAMBLING: [
    { name: "Las Vegas Sands", type: "Individual" },
    { name: "MGM Resorts", type: "PAC" },
    { name: "Caesars Entertainment", type: "PAC" },
  ],
};

// Key bills for voting records
const KEY_BILLS = [
  {
    billName: "Inflation Reduction Act",
    billId: "H.R.5376",
    date: "2022-08-07",
    description:
      "Climate, healthcare, and tax reform bill including Medicare drug price negotiation",
    corporateInterest: "Pharmaceutical and fossil fuel industries opposed the bill",
    publicImpact:
      "Caps insulin at $35/month, allows Medicare to negotiate drug prices, invests $369B in clean energy",
    industries: ["PHARMA", "OIL_GAS"],
    demVote: "Yea",
    repVote: "Nay",
  },
  {
    billName: "Tax Cuts and Jobs Act",
    billId: "H.R.1",
    date: "2017-12-20",
    description: "Major tax overhaul cutting corporate tax rate from 35% to 21%",
    corporateInterest: "Wall Street and major corporations lobbied heavily for passage",
    publicImpact: "Added $1.9 trillion to deficit while 83% of benefits went to top 1% by 2027",
    industries: ["FINANCE", "REAL_ESTATE"],
    demVote: "Nay",
    repVote: "Yea",
  },
  {
    billName: "CHIPS and Science Act",
    billId: "H.R.4346",
    date: "2022-07-27",
    description: "Provides $52B in subsidies to semiconductor manufacturers",
    corporateInterest: "Big Tech and chip manufacturers lobbied for billions in subsidies",
    publicImpact:
      "Subsidizes profitable corporations with taxpayer money; may help domestic chip supply",
    industries: ["TECH", "DEFENSE"],
    demVote: "Yea",
    repVote: "mixed",
  },
  {
    billName: "Infrastructure Investment and Jobs Act",
    billId: "H.R.3684",
    date: "2021-08-10",
    description: "Bipartisan infrastructure bill for roads, bridges, broadband",
    corporateInterest:
      "Construction and telecom industries stood to benefit from massive contracts",
    publicImpact: "Invests $1.2T in infrastructure but includes crypto tax reporting provisions",
    industries: ["CONSTRUCTION", "TELECOM"],
    demVote: "Yea",
    repVote: "mixed",
  },
  {
    billName: "Dodd-Frank Rollback (S.2155)",
    billId: "S.2155",
    date: "2018-03-14",
    description: "Rolled back banking regulations from the 2008 financial crisis",
    corporateInterest: "Banks and financial institutions lobbied heavily for deregulation",
    publicImpact: "Weakened consumer protections; SVB collapse in 2023 linked to reduced oversight",
    industries: ["FINANCE", "INSURANCE"],
    demVote: "mixed",
    repVote: "Yea",
  },
  {
    billName: "National Defense Authorization Act FY2024",
    billId: "H.R.2670",
    date: "2023-12-14",
    description: "Annual defense spending bill authorizing $886 billion for military",
    corporateInterest: "Defense contractors secured massive procurement contracts",
    publicImpact: "Nearly $1 trillion for military while domestic programs face cuts",
    industries: ["DEFENSE"],
    demVote: "mixed",
    repVote: "Yea",
  },
];

// Industry profiles by party and state characteristics
const INDUSTRY_PROFILES = {
  R_oil: ["OIL_GAS", "ENERGY", "AGRIBUSINESS", "DEFENSE", "GUNS"],
  R_finance: ["FINANCE", "INSURANCE", "REAL_ESTATE", "PHARMA", "DEFENSE"],
  R_tech: ["TECH", "CRYPTO", "FINANCE", "DEFENSE", "TELECOM"],
  D_finance: ["FINANCE", "TECH", "LAWYERS", "PHARMA", "REAL_ESTATE"],
  D_defense: ["DEFENSE", "TECH", "LAWYERS", "FINANCE", "TELECOM"],
  D_progressive: ["LAWYERS", "TECH", "AGRIBUSINESS", "ENERGY", "OTHER"],
  I_moderate: ["FINANCE", "ENERGY", "LAWYERS", "TECH", "REAL_ESTATE"],
};

// Map senators to industry profiles based on known affiliations
function getIndustryProfile(senator) {
  const { state, party, name } = senator;
  const oilStates = ["TX", "OK", "LA", "AK", "WY", "ND", "MT", "WV", "AL"];
  const techStates = ["CA", "WA", "CO", "VA", "MA"];
  const agStates = ["IA", "NE", "KS", "SD", "ND", "AR", "MS", "MT"];
  const financeStates = ["NY", "CT", "DE", "NJ"];
  const defenseStates = ["VA", "MD", "GA", "FL", "SC", "MS"];

  if (party === "I") return INDUSTRY_PROFILES.I_moderate;
  if (party === "R") {
    if (oilStates.includes(state)) return INDUSTRY_PROFILES.R_oil;
    if (name.includes("Crapo") || name.includes("Lummis")) return INDUSTRY_PROFILES.R_tech;
    return INDUSTRY_PROFILES.R_finance;
  }
  // Democrat
  if (
    name.includes("Warren") ||
    name.includes("Sanders") ||
    name.includes("Markey") ||
    name.includes("Merkley")
  )
    return INDUSTRY_PROFILES.D_progressive;
  if (defenseStates.includes(state)) return INDUSTRY_PROFILES.D_defense;
  if (financeStates.includes(state) || techStates.includes(state))
    return INDUSTRY_PROFILES.D_finance;
  return INDUSTRY_PROFILES.D_defense;
}

function rand(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function pickRandom(arr, n) {
  const shuffled = [...arr].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, n);
}

function generateSenator(data) {
  const { name, state, party, years, nick, initials } = data;
  const id = name
    .toLowerCase()
    .split(" ")
    .reverse()
    .join("-")
    .replace(/[^a-z-]/g, "");

  // Progressive / low-corruption senators get lower scores
  const isProgressive = ["Sanders", "Warren", "Markey", "Merkley", "Welch"].some((n) =>
    name.includes(n)
  );
  const isNotorious = [
    "McConnell",
    "Cruz",
    "Graham",
    "Rubio",
    "Scott",
    "Tuberville",
    "Cotton",
  ].some((n) => name.includes(n));
  const isNew = years <= 2;

  let scoreBase = party === "R" ? rand(45, 85) : party === "D" ? rand(25, 70) : rand(30, 60);
  if (isProgressive) scoreBase = rand(10, 30);
  if (isNotorious) scoreBase = rand(70, 95);
  if (isNew) scoreBase = Math.max(15, scoreBase - 15);

  const corruptionScore = {
    corporateFunding: Math.min(100, scoreBase + rand(-10, 15)),
    lobbyistAlignment: Math.min(100, scoreBase + rand(-15, 10)),
    industryConcentration: Math.min(100, scoreBase + rand(-20, 10)),
    flipFlopIndex: Math.min(100, Math.max(0, scoreBase + rand(-25, 5))),
    revolvingDoor: Math.min(100, Math.max(0, scoreBase + rand(-20, 15))),
  };

  // Funding scales with years in office
  const yearMultiplier = Math.max(1, years / 6);
  const totalRaised = Math.round(rand(5, 30) * 1_000_000 * yearMultiplier);
  const pacRatio = party === "R" ? rand(25, 50) / 100 : rand(15, 40) / 100;
  if (isProgressive) {
    var totalFromPACs = Math.round((totalRaised * rand(5, 15)) / 100);
    var smallDonorPercentage = rand(45, 72);
  } else {
    var totalFromPACs = Math.round(totalRaised * pacRatio);
    var smallDonorPercentage = rand(8, 35);
  }

  // Industry breakdown
  const profile = getIndustryProfile(data);
  const INDUSTRY_NAMES = {
    PHARMA: "Pharmaceuticals",
    INSURANCE: "Insurance",
    OIL_GAS: "Oil & Gas",
    DEFENSE: "Defense & Aerospace",
    FINANCE: "Wall Street",
    REAL_ESTATE: "Real Estate",
    TECH: "Big Tech",
    TELECOM: "Telecom",
    AGRIBUSINESS: "Agribusiness",
    ENERGY: "Energy Utilities",
    CONSTRUCTION: "Construction",
    TRANSPORT: "Transportation",
    LAWYERS: "Lawyers & Lobbyists",
    LOBBYISTS: "Lobbyists",
    GAMBLING: "Casinos & Gambling",
    GUNS: "Firearms",
    TOBACCO: "Tobacco",
    CRYPTO: "Crypto",
    PRIVATE_PRISON: "Private Prisons",
    OTHER: "Other",
  };

  const industryBreakdown = profile.slice(0, rand(3, 5)).map((ind, i) => {
    const pct = i === 0 ? rand(8, 16) : rand(3, 10);
    return {
      industry: ind,
      name: INDUSTRY_NAMES[ind],
      total: Math.round((totalRaised * pct) / 100),
      percentage: pct,
    };
  });

  // Top donors from relevant industries
  const topDonors = [];
  for (const ind of profile.slice(0, 3)) {
    const pool = DONORS_BY_INDUSTRY[ind];
    if (pool) {
      const picked = pickRandom(pool, rand(1, 2));
      for (const d of picked) {
        topDonors.push({
          name: d.name,
          total: rand(50, 500) * 1000,
          type: d.type,
        });
      }
    }
  }
  // Sort by total descending, take top 5
  topDonors.sort((a, b) => b.total - a.total);
  topDonors.splice(5);

  // Voting record
  const totalVotes = rand(800, 3500);
  const corpRate = scoreBase / 100;
  const proCorporateVotes = Math.round(totalVotes * (corpRate * 0.6 + 0.15));
  const proConsumerVotes = Math.round(totalVotes * ((1 - corpRate) * 0.4 + 0.1));

  // Pick 2-3 key votes
  const numVotes = rand(2, 3);
  const selectedBills = pickRandom(KEY_BILLS, numVotes);
  const keyVotes = selectedBills.map((bill) => {
    let vote;
    if (bill.demVote === "mixed" || bill.repVote === "mixed") {
      vote =
        party === "D"
          ? bill.demVote === "mixed"
            ? Math.random() > 0.5
              ? "Yea"
              : "Nay"
            : bill.demVote
          : bill.repVote === "mixed"
            ? Math.random() > 0.5
              ? "Yea"
              : "Nay"
            : bill.repVote;
    } else {
      vote =
        party === "R"
          ? bill.repVote
          : party === "D"
            ? bill.demVote
            : Math.random() > 0.5
              ? "Yea"
              : "Nay";
    }
    if (isNew && Math.random() > 0.7) vote = "Not Voting";

    const relIndustries = bill.industries;
    const relDonors = topDonors
      .filter((d) => {
        for (const ind of relIndustries) {
          const pool = DONORS_BY_INDUSTRY[ind];
          if (pool && pool.some((p) => p.name === d.name)) return true;
        }
        return false;
      })
      .map((d) => d.name);

    return {
      billName: bill.billName,
      billId: bill.billId,
      date: bill.date,
      vote,
      description: bill.description,
      corporateInterest: bill.corporateInterest,
      publicImpact: bill.publicImpact,
      relevantDonors: relDonors.length > 0 ? relDonors : [topDonors[0]?.name || "Unknown PAC"],
      relevantDonorTotal:
        relDonors.reduce((sum, name) => {
          const d = topDonors.find((t) => t.name === name);
          return sum + (d?.total || 0);
        }, 0) || rand(50, 300) * 1000,
    };
  });

  // Lobbying matches
  const lobbyingOrgs = [
    { org: "PhRMA", ind: "PHARMA", spend: rand(200, 400) * 100000 },
    { org: "American Petroleum Institute", ind: "OIL_GAS", spend: rand(100, 300) * 100000 },
    { org: "US Chamber of Commerce", ind: "FINANCE", spend: rand(300, 600) * 100000 },
    { org: "National Assn of Realtors", ind: "REAL_ESTATE", spend: rand(50, 200) * 100000 },
    { org: "AHIP (Health Insurers)", ind: "INSURANCE", spend: rand(100, 250) * 100000 },
    { org: "Business Roundtable", ind: "FINANCE", spend: rand(150, 350) * 100000 },
    { org: "National Mining Assn", ind: "ENERGY", spend: rand(20, 80) * 100000 },
    { org: "Telecom Industry Assn", ind: "TELECOM", spend: rand(50, 150) * 100000 },
    { org: "Defense Industry Assn", ind: "DEFENSE", spend: rand(80, 200) * 100000 },
    { org: "American Bankers Assn", ind: "FINANCE", spend: rand(100, 300) * 100000 },
    { org: "Crypto Council for Innovation", ind: "CRYPTO", spend: rand(20, 100) * 100000 },
    { org: "National Rifle Assn", ind: "GUNS", spend: rand(30, 100) * 100000 },
  ];

  const relevantLobbyOrgs = lobbyingOrgs.filter((l) => profile.includes(l.ind));
  const selectedLobby = pickRandom(
    relevantLobbyOrgs.length > 0 ? relevantLobbyOrgs : lobbyingOrgs,
    rand(2, 3)
  );

  const lobbyingMatches = selectedLobby.map((lobby) => {
    const aligned = isProgressive ? Math.random() > 0.7 : Math.random() > 0.3;
    const donation = rand(20, 300) * 1000;
    const targetBills = pickRandom(
      KEY_BILLS.filter((b) => b.industries.includes(lobby.ind)).map((b) => b.billId),
      rand(1, 2)
    );

    return {
      lobbyistOrg: lobby.org,
      industry: lobby.ind,
      lobbyingSpend: lobby.spend,
      donationToSenator: donation,
      billsInfluenced: targetBills.length > 0 ? targetBills : [KEY_BILLS[0].billId],
      senatorVoteAligned: aligned,
      description: `${lobby.org} spent ${formatMoney(lobby.spend)} lobbying Congress. ${name} received ${formatMoney(donation)} from related interests and ${aligned ? "voted in alignment with" : "voted against"} their lobbying position.`,
    };
  });

  return {
    id,
    name,
    state,
    party,
    yearsInOffice: years,
    punkNickname: nick,
    initials,
    corruptionScore,
    funding: {
      totalRaised,
      totalFromPACs,
      smallDonorPercentage,
      topDonors,
      industryBreakdown,
    },
    votingRecord: {
      totalVotes,
      proCorporateVotes,
      proConsumerVotes,
      keyVotes,
    },
    lobbyingMatches,
  };
}

function formatMoney(amount) {
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(1)}M`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(0)}K`;
  return `$${amount}`;
}

// Generate all senators
const senators = SENATOR_DATA.map(generateSenator);

// Validate
console.log(`Generated ${senators.length} senators`);
const states = new Set(senators.map((s) => s.state));
console.log(`Covering ${states.size} states`);

// Check each state has 2 senators
for (const state of states) {
  const count = senators.filter((s) => s.state === state).length;
  if (count !== 2) console.warn(`WARNING: ${state} has ${count} senators`);
}

// Write output
const outPath = join(__dirname, "..", "src", "data", "senators.json");
writeFileSync(outPath, JSON.stringify(senators, null, 2));
console.log(`Written to ${outPath}`);
