"""President service — seed data, querying, and score calculation.

Static scores are based on the C-SPAN Presidential Historians Survey (2021),
Gallup historical approval data, BEA GDP tables, BLS employment records,
and the American Presidency Project.

Dynamic scoring for recent presidents (Clinton onward) is handled by
the president pipeline, which fetches live data from the Federal Register
and BLS APIs and recalculates affected metrics.
"""

import json
import logging

from sqlalchemy.orm import Session

from app.config_definitions import PRESIDENT_SCORE_WEIGHTS
from app.models import President
from app.schemas import (
    PresidentialScoreSchema,
    PresidentLeaderboardEntry,
    PresidentSchema,
)

logger = logging.getLogger(__name__)

SEED_VERSION = 3  # bump to re-seed after schema/data changes

# fmt: off
SEED_PRESIDENTS: list[dict] = [
    # ── #1–10 Founding Era & Early Republic ──────────────────────────────
    {
        "id": "washington-1", "name": "George Washington", "party": "I", "number": 1,
        "term_start": "1789-04-30", "term_end": "1797-03-04",
        "score_independence": 85, "score_follow_through": 72,
        "score_public_mandate": 90, "score_effectiveness": 70, "score_competence": 88, "score_agency_alignment": 72,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 8, "eo_court_success_pct": None, "cabinet_turnover_pct": 20.0,
        "summary": "Set virtually every precedent for the presidency. Voluntarily stepped down after two terms, establishing the tradition of peaceful transfer of power.",
        "key_achievements": ["Established the cabinet system", "Whiskey Rebellion resolution", "Jay Treaty maintained peace with Britain", "Voluntary two-term limit precedent"],
        "key_failures": ["Owned enslaved people", "Whiskey tax was deeply unpopular on the frontier", "Partisan divide emerged between Hamilton and Jefferson"],
    },
    {
        "id": "adams-2", "name": "John Adams", "party": "F", "number": 2,
        "term_start": "1797-03-04", "term_end": "1801-03-04",
        "score_independence": 70, "score_follow_through": 50,
        "score_public_mandate": 42, "score_effectiveness": 48, "score_competence": 58, "score_agency_alignment": 55,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 1, "eo_court_success_pct": None, "cabinet_turnover_pct": 25.0,
        "summary": "Kept the nation out of war with France but the Alien and Sedition Acts remain a stain on civil liberties. Lost reelection in the first contested transfer of power.",
        "key_achievements": ["Avoided war with France", "Built the US Navy", "Peaceful transfer of power to Jefferson"],
        "key_failures": ["Alien and Sedition Acts", "Federalist Party collapsed under his leadership", "Lost reelection"],
    },
    {
        "id": "jefferson-3", "name": "Thomas Jefferson", "party": "DR", "number": 3,
        "term_start": "1801-03-04", "term_end": "1809-03-04",
        "score_independence": 68, "score_follow_through": 75,
        "score_public_mandate": 78, "score_effectiveness": 72, "score_competence": 65, "score_agency_alignment": 58,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 4, "eo_court_success_pct": None, "cabinet_turnover_pct": 15.0,
        "summary": "Doubled the nation's size with the Louisiana Purchase. Champion of individual liberty in theory, but enslaved over 600 people in practice.",
        "key_achievements": ["Louisiana Purchase", "Lewis and Clark expedition", "Reduced national debt", "Abolished the international slave trade"],
        "key_failures": ["Embargo Act devastated the economy", "Owned enslaved people", "Barbary Wars were costly"],
    },
    {
        "id": "madison-4", "name": "James Madison", "party": "DR", "number": 4,
        "term_start": "1809-03-04", "term_end": "1817-03-04",
        "score_independence": 60, "score_follow_through": 52,
        "score_public_mandate": 55, "score_effectiveness": 48, "score_competence": 58, "score_agency_alignment": 42,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 1, "eo_court_success_pct": None, "cabinet_turnover_pct": 30.0,
        "summary": "Father of the Constitution who led the nation through the War of 1812. The British burned the White House but the war ended with a surge of national pride.",
        "key_achievements": ["Survived the War of 1812", "Era of Good Feelings began", "Second Bank of the United States"],
        "key_failures": ["British burned Washington D.C.", "War of 1812 was poorly managed", "Weak wartime leadership"],
    },
    {
        "id": "monroe-5", "name": "James Monroe", "party": "DR", "number": 5,
        "term_start": "1817-03-04", "term_end": "1825-03-04",
        "score_independence": 62, "score_follow_through": 65,
        "score_public_mandate": 82, "score_effectiveness": 62, "score_competence": 65, "score_agency_alignment": 62,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 1, "eo_court_success_pct": None, "cabinet_turnover_pct": 10.0,
        "summary": "Presided over the 'Era of Good Feelings' with virtually no partisan opposition. The Monroe Doctrine defined US foreign policy for a century.",
        "key_achievements": ["Monroe Doctrine", "Era of Good Feelings unity", "Florida acquisition from Spain", "Missouri Compromise"],
        "key_failures": ["Panic of 1819 recession", "Deferred the slavery question", "Missouri Compromise was a temporary fix"],
    },
    {
        "id": "jqadams-6", "name": "John Quincy Adams", "party": "DR", "number": 6,
        "term_start": "1825-03-04", "term_end": "1829-03-04",
        "score_independence": 75, "score_follow_through": 38,
        "score_public_mandate": 30, "score_effectiveness": 42, "score_competence": 55, "score_agency_alignment": 38,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 3, "eo_court_success_pct": None, "cabinet_turnover_pct": 5.0,
        "summary": "Brilliant diplomat and visionary who proposed a national university and infrastructure program. But the 'corrupt bargain' charge doomed his presidency from day one.",
        "key_achievements": ["Proposed national infrastructure plan", "Supported science and education", "Later became anti-slavery champion in Congress"],
        "key_failures": ["'Corrupt bargain' tainted legitimacy", "Could not work with Congress", "Most of his agenda was blocked"],
    },
    {
        "id": "jackson-7", "name": "Andrew Jackson", "party": "D", "number": 7,
        "term_start": "1829-03-04", "term_end": "1837-03-04",
        "score_independence": 55, "score_follow_through": 72,
        "score_public_mandate": 75, "score_effectiveness": 50, "score_competence": 55, "score_agency_alignment": 48,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 12, "eo_court_success_pct": None, "cabinet_turnover_pct": 50.0,
        "summary": "Populist hero who expanded democracy for white men while committing genocide against Native Americans. Destroyed the national bank and reshaped presidential power.",
        "key_achievements": ["Expanded voting rights for common men", "Paid off the national debt", "Preserved the Union during nullification crisis"],
        "key_failures": ["Indian Removal Act / Trail of Tears", "Spoils system corrupted government", "Destroyed the Bank causing Panic of 1837"],
    },
    {
        "id": "vanburen-8", "name": "Martin Van Buren", "party": "D", "number": 8,
        "term_start": "1837-03-04", "term_end": "1841-03-04",
        "score_independence": 52, "score_follow_through": 35,
        "score_public_mandate": 35, "score_effectiveness": 25, "score_competence": 42, "score_agency_alignment": 38,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 10, "eo_court_success_pct": None, "cabinet_turnover_pct": 10.0,
        "summary": "Skilled politician who inherited the Panic of 1837 and was unable to stop it. Earned the nickname 'Martin Van Ruin' and lost reelection badly.",
        "key_achievements": ["Independent Treasury system", "Avoided war with Britain over Canadian border", "Established 10-hour workday for federal workers"],
        "key_failures": ["Panic of 1837 economic depression", "Could not address slavery", "Lost reelection decisively"],
    },
    {
        "id": "harrison-9", "name": "William Henry Harrison", "party": "W", "number": 9,
        "term_start": "1841-03-04", "term_end": "1841-04-04",
        "score_independence": 50, "score_follow_through": 10,
        "score_public_mandate": 55, "score_effectiveness": 10, "score_competence": 30, "score_agency_alignment": 10,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 0, "eo_court_success_pct": None, "cabinet_turnover_pct": 0.0,
        "summary": "Died 31 days into his presidency from pneumonia, making his the shortest tenure in history. His death triggered the first presidential succession crisis.",
        "key_achievements": ["Won the presidency at age 68", "Established succession precedent (via Tyler)"],
        "key_failures": ["Died after 31 days", "Gave the longest inaugural address in history in cold rain"],
    },
    {
        "id": "tyler-10", "name": "John Tyler", "party": "W", "number": 10,
        "term_start": "1841-04-04", "term_end": "1845-03-04",
        "score_independence": 60, "score_follow_through": 35,
        "score_public_mandate": 22, "score_effectiveness": 38, "score_competence": 42, "score_agency_alignment": 28,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 17, "eo_court_success_pct": None, "cabinet_turnover_pct": 70.0,
        "summary": "Established the precedent that a VP who succeeds becomes full president, not just acting. Expelled from his own party and governed without a base.",
        "key_achievements": ["Established presidential succession precedent", "Texas annexation", "Webster-Ashburton Treaty with Britain"],
        "key_failures": ["Expelled from the Whig Party", "Entire cabinet resigned except one", "Later joined the Confederacy"],
    },
    # ── #11–20 Antebellum, Civil War, & Reconstruction ──────────────────
    {
        "id": "polk-11", "name": "James K. Polk", "party": "D", "number": 11,
        "term_start": "1845-03-04", "term_end": "1849-03-04",
        "score_independence": 60, "score_follow_through": 92,
        "score_public_mandate": 55, "score_effectiveness": 65, "score_competence": 75, "score_agency_alignment": 78,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 18, "eo_court_success_pct": None, "cabinet_turnover_pct": 5.0,
        "summary": "Set four major goals, achieved all four, and left after one term as promised. One of the most effective single-term presidents in history.",
        "key_achievements": ["Acquired California and the Southwest", "Settled Oregon boundary with Britain", "Reduced tariffs", "Established independent treasury"],
        "key_failures": ["Mexican-American War was controversial", "Expansion reignited the slavery crisis", "Died three months after leaving office"],
    },
    {
        "id": "taylor-12", "name": "Zachary Taylor", "party": "W", "number": 12,
        "term_start": "1849-03-04", "term_end": "1850-07-09",
        "score_independence": 58, "score_follow_through": 25,
        "score_public_mandate": 50, "score_effectiveness": 30, "score_competence": 40, "score_agency_alignment": 25,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 5, "eo_court_success_pct": None, "cabinet_turnover_pct": 0.0,
        "summary": "War hero who died 16 months into his presidency. Surprisingly opposed the expansion of slavery despite being a slaveholder himself.",
        "key_achievements": ["Opposed expansion of slavery into new territories", "Threatened to personally lead troops against secession"],
        "key_failures": ["Died in office before accomplishing his agenda", "Had no political experience before the presidency"],
    },
    {
        "id": "fillmore-13", "name": "Millard Fillmore", "party": "W", "number": 13,
        "term_start": "1850-07-09", "term_end": "1853-03-04",
        "score_independence": 38, "score_follow_through": 40,
        "score_public_mandate": 30, "score_effectiveness": 35, "score_competence": 42, "score_agency_alignment": 42,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 12, "eo_court_success_pct": None, "cabinet_turnover_pct": 15.0,
        "summary": "Signed the Compromise of 1850 including the Fugitive Slave Act, delaying the Civil War but at a moral cost. Opened trade with Japan.",
        "key_achievements": ["Compromise of 1850 delayed Civil War", "Opened trade with Japan via Perry expedition"],
        "key_failures": ["Fugitive Slave Act enforcement", "Denied renomination by his own party", "Later ran on the nativist Know-Nothing ticket"],
    },
    {
        "id": "pierce-14", "name": "Franklin Pierce", "party": "D", "number": 14,
        "term_start": "1853-03-04", "term_end": "1857-03-04",
        "score_independence": 30, "score_follow_through": 35,
        "score_public_mandate": 28, "score_effectiveness": 22, "score_competence": 28, "score_agency_alignment": 35,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 35, "eo_court_success_pct": None, "cabinet_turnover_pct": 5.0,
        "summary": "His support for the Kansas-Nebraska Act tore apart the Democratic Party and accelerated the nation toward Civil War. Widely regarded as one of the worst presidents.",
        "key_achievements": ["Gadsden Purchase expanded southwestern territory", "Only president to retain entire original cabinet"],
        "key_failures": ["Kansas-Nebraska Act led to 'Bleeding Kansas'", "Accelerated the path to Civil War", "Denied renomination"],
    },
    {
        "id": "buchanan-15", "name": "James Buchanan", "party": "D", "number": 15,
        "term_start": "1857-03-04", "term_end": "1861-03-04",
        "score_independence": 25, "score_follow_through": 20,
        "score_public_mandate": 20, "score_effectiveness": 12, "score_competence": 18, "score_agency_alignment": 15,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 16, "eo_court_success_pct": None, "cabinet_turnover_pct": 30.0,
        "summary": "Consistently ranked the worst or near-worst president. Watched helplessly as Southern states seceded, arguing he lacked constitutional authority to stop them.",
        "key_achievements": ["Maintained some government function during secession crisis"],
        "key_failures": ["Failed to prevent secession", "Supported Dred Scott decision", "Corruption scandals in his administration", "Left Lincoln an impossible situation"],
    },
    {
        "id": "lincoln-16", "name": "Abraham Lincoln", "party": "R", "number": 16,
        "term_start": "1861-03-04", "term_end": "1865-04-15",
        "score_independence": 82, "score_follow_through": 90,
        "score_public_mandate": 72, "score_effectiveness": 85, "score_competence": 92, "score_agency_alignment": 75,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 48, "eo_court_success_pct": None, "cabinet_turnover_pct": 35.0,
        "summary": "Preserved the Union and abolished slavery through the Civil War. Consistently ranked the greatest or second-greatest president by historians.",
        "key_achievements": ["Emancipation Proclamation", "Won the Civil War and preserved the Union", "13th Amendment abolishing slavery", "Gettysburg Address redefined American purpose"],
        "key_failures": ["Suspended habeas corpus", "Early war generals were ineffective", "Assassinated before Reconstruction could be guided"],
    },
    {
        "id": "ajohnson-17", "name": "Andrew Johnson", "party": "D", "number": 17,
        "term_start": "1865-04-15", "term_end": "1869-03-04",
        "score_independence": 40, "score_follow_through": 22,
        "score_public_mandate": 18, "score_effectiveness": 18, "score_competence": 20, "score_agency_alignment": 18,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 79, "eo_court_success_pct": None, "cabinet_turnover_pct": 40.0,
        "summary": "Vetoed civil rights legislation and obstructed Reconstruction at every turn. First president to be impeached. His lenient policies emboldened the former Confederacy.",
        "key_achievements": ["Alaska Purchase", "Kept some continuity after Lincoln's assassination"],
        "key_failures": ["Vetoed Civil Rights Act and Freedmen's Bureau", "Impeached by the House", "Sabotaged Reconstruction", "Emboldened former Confederate leaders"],
    },
    {
        "id": "grant-18", "name": "Ulysses S. Grant", "party": "R", "number": 18,
        "term_start": "1869-03-04", "term_end": "1877-03-04",
        "score_independence": 48, "score_follow_through": 58,
        "score_public_mandate": 52, "score_effectiveness": 48, "score_competence": 42, "score_agency_alignment": 35,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 217, "eo_court_success_pct": None, "cabinet_turnover_pct": 45.0,
        "summary": "Civil War hero who fought for civil rights during Reconstruction but whose administration was plagued by corruption scandals. Recently reassessed more favorably by historians.",
        "key_achievements": ["Crushed the KKK with Enforcement Acts", "15th Amendment ratified", "Promoted peace with Native Americans initially", "Treaty of Washington resolved tensions with Britain"],
        "key_failures": ["Widespread corruption scandals (Credit Mobilier, Whiskey Ring)", "Panic of 1873 depression", "Reconstruction gains eroded by end of term"],
    },
    {
        "id": "hayes-19", "name": "Rutherford B. Hayes", "party": "R", "number": 19,
        "term_start": "1877-03-04", "term_end": "1881-03-04",
        "score_independence": 58, "score_follow_through": 42,
        "score_public_mandate": 30, "score_effectiveness": 42, "score_competence": 52, "score_agency_alignment": 48,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 92, "eo_court_success_pct": None, "cabinet_turnover_pct": 15.0,
        "summary": "Won the most disputed election in US history through the Compromise of 1877, which ended Reconstruction and abandoned Black Southerners for a generation.",
        "key_achievements": ["Civil service reform efforts", "Ended railroad strikes", "Began modernizing federal workforce"],
        "key_failures": ["Compromise of 1877 ended Reconstruction", "Disputed election undermined legitimacy", "Abandoned civil rights in the South"],
    },
    {
        "id": "garfield-20", "name": "James A. Garfield", "party": "R", "number": 20,
        "term_start": "1881-03-04", "term_end": "1881-09-19",
        "score_independence": 60, "score_follow_through": 15,
        "score_public_mandate": 48, "score_effectiveness": 15, "score_competence": 35, "score_agency_alignment": 15,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 6, "eo_court_success_pct": None, "cabinet_turnover_pct": 0.0,
        "summary": "Assassinated six months into office by a disgruntled office seeker. His death galvanized the civil service reform movement that his successor championed.",
        "key_achievements": ["Appointed reformers to key positions", "Challenged party machine bosses", "His death led to civil service reform"],
        "key_failures": ["Assassinated before accomplishing his agenda", "Only served 200 days"],
    },
    # ── #21–31 Gilded Age & Progressive Era ──────────────────────────────
    {
        "id": "arthur-21", "name": "Chester A. Arthur", "party": "R", "number": 21,
        "term_start": "1881-09-19", "term_end": "1885-03-04",
        "score_independence": 62, "score_follow_through": 52,
        "score_public_mandate": 40, "score_effectiveness": 45, "score_competence": 55, "score_agency_alignment": 60,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 96, "eo_court_success_pct": None, "cabinet_turnover_pct": 20.0,
        "summary": "Former spoils system politician who shocked everyone by championing civil service reform after becoming president. Signed the Pendleton Act.",
        "key_achievements": ["Pendleton Civil Service Reform Act", "Modernized the US Navy", "Surprised critics by governing honestly"],
        "key_failures": ["Chinese Exclusion Act", "Could not win his own party's nomination", "Limited vision beyond reform"],
    },
    {
        "id": "cleveland-22", "name": "Grover Cleveland", "party": "D", "number": 22,
        "term_start": "1885-03-04", "term_end": "1889-03-04",
        "score_independence": 65, "score_follow_through": 55,
        "score_public_mandate": 48, "score_effectiveness": 48, "score_competence": 58, "score_agency_alignment": 55,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 113, "eo_court_success_pct": None, "cabinet_turnover_pct": 10.0,
        "summary": "First Democrat elected after the Civil War. Known for integrity and vetoing pork-barrel legislation. Lost reelection despite winning the popular vote.",
        "key_achievements": ["Vetoed hundreds of fraudulent pension bills", "Interstate Commerce Act", "Dawes Act reform attempt"],
        "key_failures": ["Lost reelection", "Limited response to labor unrest", "Dawes Act harmed Native Americans in practice"],
    },
    {
        "id": "bharrison-23", "name": "Benjamin Harrison", "party": "R", "number": 23,
        "term_start": "1889-03-04", "term_end": "1893-03-04",
        "score_independence": 40, "score_follow_through": 48,
        "score_public_mandate": 35, "score_effectiveness": 40, "score_competence": 48, "score_agency_alignment": 42,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 143, "eo_court_success_pct": None, "cabinet_turnover_pct": 15.0,
        "summary": "Won the presidency despite losing the popular vote. Signed the Sherman Antitrust Act but was seen as cold and beholden to industrialists.",
        "key_achievements": ["Sherman Antitrust Act", "McKinley Tariff", "National forest reserves created", "First Pan-American Conference"],
        "key_failures": ["Lost popular vote", "Spending earned nickname 'Billion Dollar Congress'", "Lost reelection to Cleveland"],
    },
    {
        "id": "cleveland-24", "name": "Grover Cleveland", "party": "D", "number": 24,
        "term_start": "1893-03-04", "term_end": "1897-03-04",
        "score_independence": 62, "score_follow_through": 42,
        "score_public_mandate": 35, "score_effectiveness": 28, "score_competence": 48, "score_agency_alignment": 38,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 140, "eo_court_success_pct": None, "cabinet_turnover_pct": 15.0,
        "summary": "Only president to serve non-consecutive terms. Second term was dominated by the Panic of 1893 and the Pullman Strike. Left office deeply unpopular.",
        "key_achievements": ["Maintained the gold standard", "Only non-consecutive two-term president"],
        "key_failures": ["Panic of 1893 depression", "Pullman Strike — sent federal troops against workers", "Lost support of his own party"],
    },
    {
        "id": "mckinley-25", "name": "William McKinley", "party": "R", "number": 25,
        "term_start": "1897-03-04", "term_end": "1901-09-14",
        "score_independence": 40, "score_follow_through": 60,
        "score_public_mandate": 60, "score_effectiveness": 58, "score_competence": 55, "score_agency_alignment": 52,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 185, "eo_court_success_pct": None, "cabinet_turnover_pct": 10.0,
        "summary": "Presided over economic recovery and the Spanish-American War, making the US an imperial power. Assassinated at the start of his second term.",
        "key_achievements": ["Economic recovery from 1890s depression", "Spanish-American War victory", "Gold Standard Act", "Open Door Policy with China"],
        "key_failures": ["Philippine-American War atrocities", "Close ties to industrial trusts", "Assassinated in 1901"],
    },
    {
        "id": "troosevelt-26", "name": "Theodore Roosevelt", "party": "R", "number": 26,
        "term_start": "1901-09-14", "term_end": "1909-03-04",
        "score_independence": 78, "score_follow_through": 82,
        "score_public_mandate": 80, "score_effectiveness": 75, "score_competence": 80, "score_agency_alignment": 82,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 1081, "eo_court_success_pct": None, "cabinet_turnover_pct": 20.0,
        "summary": "Trust-buster who transformed the presidency into a 'bully pulpit.' Built the Panama Canal, created the National Parks system, and won the Nobel Peace Prize.",
        "key_achievements": ["Trust-busting (broke up Standard Oil, etc.)", "Panama Canal", "National Parks and conservation", "Nobel Peace Prize for ending Russo-Japanese War"],
        "key_failures": ["Paternalistic views on race", "Panama Canal acquisition was ethically questionable", "Bull Moose run split the Republican Party"],
    },
    {
        "id": "taft-27", "name": "William Howard Taft", "party": "R", "number": 27,
        "term_start": "1909-03-04", "term_end": "1913-03-04",
        "score_independence": 50, "score_follow_through": 45,
        "score_public_mandate": 32, "score_effectiveness": 45, "score_competence": 55, "score_agency_alignment": 58,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 724, "eo_court_success_pct": None, "cabinet_turnover_pct": 20.0,
        "summary": "Actually busted more trusts than Roosevelt but lacked the political skills to get credit. Lost reelection badly, later became Chief Justice of the Supreme Court.",
        "key_achievements": ["More antitrust prosecutions than Roosevelt", "16th Amendment (income tax)", "Department of Labor established"],
        "key_failures": ["Payne-Aldrich Tariff angered progressives", "Fired conservation chief Pinchot", "Lost reelection in a three-way race"],
    },
    {
        "id": "wilson-28", "name": "Woodrow Wilson", "party": "D", "number": 28,
        "term_start": "1913-03-04", "term_end": "1921-03-04",
        "score_independence": 55, "score_follow_through": 65,
        "score_public_mandate": 55, "score_effectiveness": 55, "score_competence": 52, "score_agency_alignment": 68,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 1803, "eo_court_success_pct": None, "cabinet_turnover_pct": 25.0,
        "summary": "Led America through WWI and championed the League of Nations, which the Senate rejected. Suffered a debilitating stroke; his wife effectively governed for months.",
        "key_achievements": ["Federal Reserve System", "Led US through WWI", "League of Nations vision", "Clayton Antitrust Act", "19th Amendment (women's suffrage) advanced"],
        "key_failures": ["Senate rejected League of Nations", "Debilitating stroke — incapacity hidden", "Resegregated the federal workforce", "Espionage and Sedition Acts"],
    },
    {
        "id": "harding-29", "name": "Warren G. Harding", "party": "R", "number": 29,
        "term_start": "1921-03-04", "term_end": "1923-08-02",
        "score_independence": 22, "score_follow_through": 30,
        "score_public_mandate": 48, "score_effectiveness": 35, "score_competence": 18, "score_agency_alignment": 20,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 522, "eo_court_success_pct": None, "cabinet_turnover_pct": 15.0,
        "summary": "Promised a 'return to normalcy' but his administration became synonymous with corruption. Teapot Dome was the biggest scandal until Watergate. Died in office.",
        "key_achievements": ["Washington Naval Conference arms limitation", "Budget and Accounting Act", "Freed political prisoner Eugene Debs"],
        "key_failures": ["Teapot Dome scandal", "Rampant cronyism and corruption", "Died in office amid emerging scandals"],
    },
    {
        "id": "coolidge-30", "name": "Calvin Coolidge", "party": "R", "number": 30,
        "term_start": "1923-08-02", "term_end": "1929-03-04",
        "score_independence": 52, "score_follow_through": 55,
        "score_public_mandate": 58, "score_effectiveness": 50, "score_competence": 55, "score_agency_alignment": 48,
        "avg_approval": None, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 1203, "eo_court_success_pct": None, "cabinet_turnover_pct": 20.0,
        "summary": "'Silent Cal' restored trust after Harding's scandals through personal integrity. Presided over the Roaring Twenties boom but his laissez-faire policies set the stage for the crash.",
        "key_achievements": ["Restored public trust after scandals", "Roaring Twenties prosperity", "Revenue Act cut taxes", "Immigration Act of 1924"],
        "key_failures": ["Laissez-faire policies enabled speculation", "Ignored growing inequality", "Immigration Act was racially discriminatory"],
    },
    {
        "id": "hoover-31", "name": "Herbert Hoover", "party": "R", "number": 31,
        "term_start": "1929-03-04", "term_end": "1933-03-04",
        "score_independence": 52, "score_follow_through": 35,
        "score_public_mandate": 22, "score_effectiveness": 18, "score_competence": 35, "score_agency_alignment": 32,
        "avg_approval": None, "gdp_growth_avg": -8.0, "jobs_created_millions": None,
        "eo_count": 968, "eo_court_success_pct": None, "cabinet_turnover_pct": 10.0,
        "summary": "Brilliant humanitarian and engineer who was overwhelmed by the Great Depression. His initial interventions were too small and too late. 'Hoovervilles' became his legacy.",
        "key_achievements": ["Reconstruction Finance Corporation", "Hoover Dam construction began", "Star-Spangled Banner became national anthem"],
        "key_failures": ["Great Depression deepened on his watch", "Smoot-Hawley Tariff worsened the crisis", "Bonus Army crackdown was a PR disaster"],
    },
    # ── #32–47 Modern Presidents (FDR onward) ────────────────────────────
    {
        "id": "fdr-32", "name": "Franklin D. Roosevelt", "party": "D", "number": 32,
        "term_start": "1933-03-04", "term_end": "1945-04-12",
        "score_independence": 65, "score_follow_through": 85,
        "score_public_mandate": 75, "score_effectiveness": 72, "score_competence": 80, "score_agency_alignment": 88,
        "avg_approval": 63.0, "gdp_growth_avg": 9.4, "jobs_created_millions": None,
        "eo_count": 3721, "eo_court_success_pct": None, "cabinet_turnover_pct": None,
        "summary": "Reshaped the federal government through the New Deal, guiding the nation through the Great Depression and most of World War II. Unprecedented four terms.",
        "key_achievements": ["New Deal economic recovery programs", "Social Security Act", "Led Allied war effort in WWII", "Created the SEC and FDIC"],
        "key_failures": ["Japanese American internment", "Court-packing scheme", "Failed to address civil rights"],
    },
    {
        "id": "truman-33", "name": "Harry S. Truman", "party": "D", "number": 33,
        "term_start": "1945-04-12", "term_end": "1953-01-20",
        "score_independence": 62, "score_follow_through": 60,
        "score_public_mandate": 45, "score_effectiveness": 58, "score_competence": 65, "score_agency_alignment": 65,
        "avg_approval": 45.4, "gdp_growth_avg": 1.3, "jobs_created_millions": 8.4,
        "eo_count": 907, "eo_court_success_pct": None, "cabinet_turnover_pct": None,
        "summary": "Managed the transition from war to peace, established the Truman Doctrine and Marshall Plan. Desegregated the military via executive order.",
        "key_achievements": ["Marshall Plan for European recovery", "NATO creation", "Desegregated the armed forces", "Berlin Airlift"],
        "key_failures": ["Korean War stalemate", "Low approval by end of term", "Failed to pass national healthcare"],
    },
    {
        "id": "eisenhower-34", "name": "Dwight D. Eisenhower", "party": "R", "number": 34,
        "term_start": "1953-01-20", "term_end": "1961-01-20",
        "score_independence": 50, "score_follow_through": 68,
        "score_public_mandate": 65, "score_effectiveness": 60, "score_competence": 75, "score_agency_alignment": 70,
        "avg_approval": 65.0, "gdp_growth_avg": 3.0, "jobs_created_millions": 3.5,
        "eo_count": 484, "eo_court_success_pct": None, "cabinet_turnover_pct": 25.0,
        "summary": "Military hero turned president who built the Interstate Highway System, managed Cold War tensions, and warned of the military-industrial complex.",
        "key_achievements": ["Interstate Highway System", "Ended Korean War", "NASA creation", "Civil Rights Act of 1957"],
        "key_failures": ["U-2 spy plane incident", "Limited civil rights enforcement", "Supported coups in Iran and Guatemala"],
    },
    {
        "id": "jfk-35", "name": "John F. Kennedy", "party": "D", "number": 35,
        "term_start": "1961-01-20", "term_end": "1963-11-22",
        "score_independence": 62, "score_follow_through": 52,
        "score_public_mandate": 70, "score_effectiveness": 62, "score_competence": 58, "score_agency_alignment": 58,
        "avg_approval": 70.1, "gdp_growth_avg": 4.3, "jobs_created_millions": 3.6,
        "eo_count": 214, "eo_court_success_pct": None, "cabinet_turnover_pct": 8.0,
        "summary": "Inspired a generation with the space race and Peace Corps. Managed the Cuban Missile Crisis but presidency was cut short by assassination.",
        "key_achievements": ["Cuban Missile Crisis resolution", "Peace Corps creation", "Space program acceleration", "Nuclear Test Ban Treaty"],
        "key_failures": ["Bay of Pigs invasion", "Slow on civil rights legislation", "Vietnam escalation began"],
    },
    {
        "id": "lbj-36", "name": "Lyndon B. Johnson", "party": "D", "number": 36,
        "term_start": "1963-11-22", "term_end": "1969-01-20",
        "score_independence": 58, "score_follow_through": 78,
        "score_public_mandate": 55, "score_effectiveness": 65, "score_competence": 68, "score_agency_alignment": 78,
        "avg_approval": 55.1, "gdp_growth_avg": 5.3, "jobs_created_millions": 9.9,
        "eo_count": 325, "eo_court_success_pct": None, "cabinet_turnover_pct": 30.0,
        "summary": "Master legislator who passed landmark civil rights and anti-poverty laws. The Great Society transformed domestic policy but Vietnam destroyed his presidency.",
        "key_achievements": ["Civil Rights Act of 1964", "Voting Rights Act of 1965", "Medicare and Medicaid", "Great Society anti-poverty programs"],
        "key_failures": ["Vietnam War escalation", "Credibility gap with public", "Urban unrest"],
    },
    {
        "id": "nixon-37", "name": "Richard Nixon", "party": "R", "number": 37,
        "term_start": "1969-01-20", "term_end": "1974-08-09",
        "score_independence": 42, "score_follow_through": 55,
        "score_public_mandate": 49, "score_effectiveness": 42, "score_competence": 38, "score_agency_alignment": 62,
        "avg_approval": 49.1, "gdp_growth_avg": 3.0, "jobs_created_millions": 6.2,
        "eo_count": 346, "eo_court_success_pct": None, "cabinet_turnover_pct": 35.0,
        "summary": "Opened relations with China and created the EPA, but Watergate remains the defining scandal of the American presidency. Resigned to avoid impeachment.",
        "key_achievements": ["Opening to China", "EPA creation", "Ended Vietnam War draft", "OSHA creation"],
        "key_failures": ["Watergate scandal and resignation", "Cambodia bombing", "Wage and price controls"],
    },
    {
        "id": "ford-38", "name": "Gerald Ford", "party": "R", "number": 38,
        "term_start": "1974-08-09", "term_end": "1977-01-20",
        "score_independence": 55, "score_follow_through": 40,
        "score_public_mandate": 47, "score_effectiveness": 38, "score_competence": 55, "score_agency_alignment": 45,
        "avg_approval": 47.2, "gdp_growth_avg": 2.6, "jobs_created_millions": 1.8,
        "eo_count": 169, "eo_court_success_pct": None, "cabinet_turnover_pct": 20.0,
        "summary": "Unelected president who inherited a nation in crisis after Watergate. The Nixon pardon was courageous but politically devastating.",
        "key_achievements": ["Restored public trust post-Watergate", "Helsinki Accords", "Whip Inflation Now campaign"],
        "key_failures": ["Nixon pardon backlash", "Fall of Saigon", "Stagflation continued"],
    },
    {
        "id": "carter-39", "name": "Jimmy Carter", "party": "D", "number": 39,
        "term_start": "1977-01-20", "term_end": "1981-01-20",
        "score_independence": 72, "score_follow_through": 45,
        "score_public_mandate": 45, "score_effectiveness": 35, "score_competence": 42, "score_agency_alignment": 52,
        "avg_approval": 45.5, "gdp_growth_avg": 3.3, "jobs_created_millions": 10.3,
        "eo_count": 320, "eo_court_success_pct": None, "cabinet_turnover_pct": 30.0,
        "summary": "Washington outsider who brokered Camp David Accords but was overwhelmed by the energy crisis, inflation, and Iran hostage situation.",
        "key_achievements": ["Camp David Accords", "Department of Energy creation", "Human rights foreign policy", "Panama Canal Treaty"],
        "key_failures": ["Iran hostage crisis", "Energy crisis mismanagement", "Malaise speech backlash", "Double-digit inflation"],
    },
    {
        "id": "reagan-40", "name": "Ronald Reagan", "party": "R", "number": 40,
        "term_start": "1981-01-20", "term_end": "1989-01-20",
        "score_independence": 38, "score_follow_through": 72,
        "score_public_mandate": 53, "score_effectiveness": 58, "score_competence": 60, "score_agency_alignment": 55,
        "avg_approval": 52.8, "gdp_growth_avg": 3.5, "jobs_created_millions": 16.0,
        "eo_count": 381, "eo_court_success_pct": None, "cabinet_turnover_pct": 40.0,
        "summary": "Defined modern conservatism with tax cuts, deregulation, and defense buildup. Won the Cold War but tripled the national debt and Iran-Contra scarred the second term.",
        "key_achievements": ["Economic recovery from 1982 recession", "Cold War victory", "Tax Reform Act of 1986", "INF Treaty with USSR"],
        "key_failures": ["Iran-Contra affair", "Tripled national debt", "Slow AIDS response", "Savings and loan crisis"],
    },
    {
        "id": "bush-41", "name": "George H.W. Bush", "party": "R", "number": 41,
        "term_start": "1989-01-20", "term_end": "1993-01-20",
        "score_independence": 48, "score_follow_through": 48,
        "score_public_mandate": 61, "score_effectiveness": 45, "score_competence": 65, "score_agency_alignment": 60,
        "avg_approval": 60.9, "gdp_growth_avg": 2.2, "jobs_created_millions": 2.6,
        "eo_count": 166, "eo_court_success_pct": None, "cabinet_turnover_pct": 15.0,
        "summary": "Expert foreign policy president who managed the end of the Cold War and Gulf War coalition. 'Read my lips: no new taxes' broken promise cost him reelection.",
        "key_achievements": ["German reunification management", "Gulf War coalition", "Americans with Disabilities Act", "Clean Air Act amendments"],
        "key_failures": ["'Read my lips' broken tax pledge", "Recession of 1990-91", "Perceived as out of touch on economy"],
    },
    {
        "id": "clinton-42", "name": "Bill Clinton", "party": "D", "number": 42,
        "term_start": "1993-01-20", "term_end": "2001-01-20",
        "score_independence": 52, "score_follow_through": 68,
        "score_public_mandate": 55, "score_effectiveness": 78, "score_competence": 62, "score_agency_alignment": 70,
        "avg_approval": 55.1, "gdp_growth_avg": 3.9, "jobs_created_millions": 22.7,
        "eo_count": 364, "eo_court_success_pct": None, "cabinet_turnover_pct": 35.0,
        "summary": "Presided over the longest peacetime economic expansion in US history and balanced the federal budget. Impeachment over the Lewinsky scandal defined the second term.",
        "key_achievements": ["Balanced federal budget", "22.7M jobs created", "NAFTA", "Welfare reform"],
        "key_failures": ["Impeachment", "Failed healthcare reform", "Rwandan genocide inaction", "Deregulation contributed to 2008 crisis"],
    },
    {
        "id": "gwbush-43", "name": "George W. Bush", "party": "R", "number": 43,
        "term_start": "2001-01-20", "term_end": "2009-01-20",
        "score_independence": 35, "score_follow_through": 55,
        "score_public_mandate": 49, "score_effectiveness": 38, "score_competence": 42, "score_agency_alignment": 45,
        "avg_approval": 49.4, "gdp_growth_avg": 2.1, "jobs_created_millions": 1.3,
        "eo_count": 291, "eo_court_success_pct": 75.0, "cabinet_turnover_pct": 40.0,
        "summary": "United the nation after 9/11 but Iraq War and Hurricane Katrina eroded trust. Presidency ended with the worst financial crisis since the Great Depression.",
        "key_achievements": ["Post-9/11 national unity", "PEPFAR (AIDS relief in Africa)", "Medicare Part D", "No Child Left Behind"],
        "key_failures": ["Iraq War based on faulty intelligence", "Hurricane Katrina response", "Great Recession began", "Guantanamo and torture controversies"],
    },
    {
        "id": "obama-44", "name": "Barack Obama", "party": "D", "number": 44,
        "term_start": "2009-01-20", "term_end": "2017-01-20",
        "score_independence": 55, "score_follow_through": 62,
        "score_public_mandate": 48, "score_effectiveness": 55, "score_competence": 65, "score_agency_alignment": 65,
        "avg_approval": 47.9, "gdp_growth_avg": 1.6, "jobs_created_millions": 11.6,
        "eo_count": 276, "eo_court_success_pct": 78.0, "cabinet_turnover_pct": 30.0,
        "summary": "First Black president who passed the Affordable Care Act and led economic recovery from the Great Recession. Faced historic congressional obstruction.",
        "key_achievements": ["Affordable Care Act", "Economic recovery (11.6M jobs)", "Paris Climate Agreement", "Bin Laden operation"],
        "key_failures": ["ACA rollout problems", "Syria red line", "Could not close Guantanamo", "Rising partisanship"],
    },
    {
        "id": "trump-45", "name": "Donald Trump", "party": "R", "number": 45,
        "term_start": "2017-01-20", "term_end": "2021-01-20",
        "score_independence": 30, "score_follow_through": 48,
        "score_public_mandate": 41, "score_effectiveness": 48, "score_competence": 35, "score_agency_alignment": 35,
        "avg_approval": 41.1, "gdp_growth_avg": 1.5, "jobs_created_millions": -2.7,
        "eo_count": 220, "eo_court_success_pct": 55.0, "cabinet_turnover_pct": 65.0,
        "summary": "Populist who cut taxes and reshaped federal judiciary. Pre-pandemic economy was strong but COVID-19 response and January 6th defined the final year.",
        "key_achievements": ["Tax Cuts and Jobs Act", "Abraham Accords", "Operation Warp Speed vaccines", "USMCA trade deal"],
        "key_failures": ["COVID-19 pandemic response", "January 6th Capitol breach", "Record cabinet turnover", "Many EOs blocked by courts"],
    },
    {
        "id": "biden-46", "name": "Joe Biden", "party": "D", "number": 46,
        "term_start": "2021-01-20", "term_end": "2025-01-20",
        "score_independence": 55, "score_follow_through": 52,
        "score_public_mandate": 41, "score_effectiveness": 48, "score_competence": 52, "score_agency_alignment": 58,
        "avg_approval": 40.8, "gdp_growth_avg": 3.4, "jobs_created_millions": 16.6,
        "eo_count": 162, "eo_court_success_pct": 70.0, "cabinet_turnover_pct": 20.0,
        "summary": "Passed major infrastructure and climate legislation. Record job creation but persistent inflation eroded public confidence. Withdrew from reelection.",
        "key_achievements": ["Bipartisan Infrastructure Law", "CHIPS Act", "Inflation Reduction Act (climate)", "Record job creation"],
        "key_failures": ["Afghanistan withdrawal", "Persistent inflation", "Border crisis", "Low approval despite economic data"],
    },
    {
        "id": "trump-47", "name": "Donald Trump", "party": "R", "number": 47,
        "term_start": "2025-01-20", "term_end": None, "is_current": True,
        "score_independence": 30, "score_follow_through": 50,
        "score_public_mandate": 48, "score_effectiveness": 45, "score_competence": 38, "score_agency_alignment": 28,
        "avg_approval": 47.0, "gdp_growth_avg": None, "jobs_created_millions": None,
        "eo_count": 80, "eo_court_success_pct": 45.0, "cabinet_turnover_pct": 10.0,
        "summary": "Second non-consecutive term focused on tariff policy, government restructuring (DOGE), and aggressive executive action. Many orders face legal challenges.",
        "key_achievements": ["Aggressive executive action pace", "Government restructuring initiative", "Tariff-based trade policy"],
        "key_failures": ["Multiple EOs blocked by courts", "Tariff-driven market volatility", "Agency staffing disruptions"],
    },
]
# fmt: on


def seed_presidents(db: Session) -> int:
    """Populate/refresh the presidents table when seed version changes."""
    existing = db.query(President).count()

    # Check if we need to re-seed (version bump = more data added)
    if existing > 0:
        from app.pipeline.cache import api_cache_get, api_cache_set
        cached_ver = api_cache_get(db, "meta", "president_seed_version")
        if cached_ver and int(cached_ver.get("v", 0)) >= SEED_VERSION:
            return 0
        # Wipe and re-seed
        db.query(President).delete()
        db.commit()
        logger.info("Re-seeding presidents (version %d)", SEED_VERSION)

    count = 0
    for data in SEED_PRESIDENTS:
        president = President(
            id=data["id"],
            name=data["name"],
            party=data["party"],
            number=data["number"],
            term_start=data["term_start"],
            term_end=data.get("term_end"),
            is_current=data.get("is_current", False),
            score_independence=data.get("score_independence", 50),
            score_follow_through=data.get("score_follow_through", 50),
            score_public_mandate=data.get("score_public_mandate", 50),
            score_effectiveness=data.get("score_effectiveness", 50),
            score_competence=data.get("score_competence", 50),
            score_agency_alignment=data.get("score_agency_alignment", 50),
            avg_approval=data.get("avg_approval"),
            gdp_growth_avg=data.get("gdp_growth_avg"),
            jobs_created_millions=data.get("jobs_created_millions"),
            eo_count=data.get("eo_count"),
            eo_court_success_pct=data.get("eo_court_success_pct"),
            cabinet_turnover_pct=data.get("cabinet_turnover_pct"),
            summary=data.get("summary", ""),
            key_achievements=json.dumps(data.get("key_achievements", [])),
            key_failures=json.dumps(data.get("key_failures", [])),
        )
        db.add(president)
        count += 1

    db.commit()

    # Store version
    from app.pipeline.cache import api_cache_set
    api_cache_set(db, "meta", "president_seed_version", {"v": SEED_VERSION})

    logger.info("Seeded %d presidents (version %d)", count, SEED_VERSION)
    return count


def _competence_has_live_data(p: President) -> bool:
    """True if this president's Competence score blended in real EO-rate
    data rather than being pure seed (see calc_competence — court-success
    and cabinet-turnover rates never have a live source, so this can only
    ever reflect the EO-activity-rate component)."""
    from app.pipeline.president_pipeline import DYNAMIC_PRESIDENTS
    return p.id in DYNAMIC_PRESIDENTS and p.eo_count is not None


def _build_response(p: President) -> PresidentSchema:
    return PresidentSchema(
        id=p.id,
        name=p.name,
        party=p.party,
        number=p.number,
        term_start=p.term_start,
        term_end=p.term_end,
        is_current=p.is_current,
        score=PresidentialScoreSchema(
            independence=p.score_independence,
            follow_through=p.score_follow_through,
            public_mandate=p.score_public_mandate,
            effectiveness=p.score_effectiveness,
            competence=p.score_competence,
            agency_alignment=p.score_agency_alignment,
        ),
        avg_approval=p.avg_approval,
        gdp_growth_avg=p.gdp_growth_avg,
        jobs_created_millions=p.jobs_created_millions,
        eo_count=p.eo_count,
        eo_court_success_pct=p.eo_court_success_pct,
        cabinet_turnover_pct=p.cabinet_turnover_pct,
        competence_has_live_data=_competence_has_live_data(p),
        summary=p.summary,
        key_achievements=json.loads(p.key_achievements) if p.key_achievements else [],
        key_failures=json.loads(p.key_failures) if p.key_failures else [],
    )


def get_president(db: Session, president_id: str) -> PresidentSchema | None:
    p = db.query(President).filter(President.id == president_id).first()
    if not p:
        return None
    return _build_response(p)


def get_president_score_breakdown(db: Session, president_id: str) -> dict | None:
    """Recompute a president's full score-derivation breakdown on-demand.

    Competence/Effectiveness/Agency Alignment use the _core variants of
    president_scorer.py's calc_* functions with whatever live-data columns
    are currently stored (gdp_growth_adjusted, rulemaking_count,
    rulemaking_finalized_pct — persisted by president_pipeline.py
    specifically so this recompute is possible; previously only kept in a
    local dict and discarded).

    Gated on DYNAMIC_PRESIDENTS/ECONOMICS_ONLY_PRESIDENTS membership, the
    same as president_pipeline.py itself — some presidents have seed-data
    values in eo_count/gdp_growth_avg/etc. that were never actually fed
    through a live formula (that pipeline never touches their Competence/
    Effectiveness/Agency Alignment), so gating on stored-value presence
    alone would fabricate a "live" breakdown that was never really
    computed for them.

    Independence/Follow-Through/Public Mandate have no calc function at
    all — always pure editorial seed values (see president_scorer.py
    module docstring) — represented as {"seedOnly": True} for the frontend
    to render as "editorial estimate," not a formula breakdown.
    """
    from app.pipeline.analyze.president_scorer import (
        _agency_alignment_core,
        _competence_core,
        _effectiveness_core,
    )
    from app.pipeline.president_pipeline import (
        DYNAMIC_PRESIDENTS,
        ECONOMICS_ONLY_PRESIDENTS,
        _term_years,
    )

    p = db.query(President).filter(President.id == president_id).first()
    if not p:
        return None

    term_years = _term_years(p.term_start, p.term_end)
    is_dynamic = p.id in DYNAMIC_PRESIDENTS
    is_econ = is_dynamic or p.id in ECONOMICS_ONLY_PRESIDENTS

    def _seed_only(score: float) -> dict:
        return {"score": score, "seedOnly": True}

    return {
        "independence": _seed_only(p.score_independence),
        "followThrough": _seed_only(p.score_follow_through),
        "publicMandate": _seed_only(p.score_public_mandate),
        "competence": (
            _competence_core(
                eo_count=p.eo_count,
                eo_court_success_pct=p.eo_court_success_pct,
                cabinet_turnover_pct=p.cabinet_turnover_pct,
                term_years=term_years,
                seed_score=p.score_competence,
            )
            if is_dynamic else _seed_only(p.score_competence)
        ),
        "effectiveness": (
            _effectiveness_core(
                jobs_created_millions=p.jobs_created_millions,
                gdp_growth_avg=p.gdp_growth_avg,
                term_years=term_years,
                seed_score=p.score_effectiveness,
                gdp_growth_adjusted=p.gdp_growth_adjusted,
            )
            if is_econ else _seed_only(p.score_effectiveness)
        ),
        "agencyAlignment": (
            _agency_alignment_core(
                rulemaking_count=p.rulemaking_count,
                rulemaking_finalized_pct=p.rulemaking_finalized_pct,
                term_years=term_years,
                seed_score=p.score_agency_alignment,
            )
            if is_dynamic else _seed_only(p.score_agency_alignment)
        ),
    }


def get_all_presidents(db: Session) -> list[PresidentSchema]:
    presidents = db.query(President).order_by(President.number.desc()).all()
    return [_build_response(p) for p in presidents]


def get_president_leaderboard(db: Session) -> list[PresidentLeaderboardEntry]:
    presidents = db.query(President).all()
    entries = []
    for p in presidents:
        score = PresidentialScoreSchema(
            independence=p.score_independence,
            follow_through=p.score_follow_through,
            public_mandate=p.score_public_mandate,
            effectiveness=p.score_effectiveness,
            competence=p.score_competence,
            agency_alignment=p.score_agency_alignment,
        )
        entries.append(PresidentLeaderboardEntry(
            id=p.id,
            name=p.name,
            party=p.party,
            number=p.number,
            term_start=p.term_start,
            term_end=p.term_end,
            is_current=p.is_current,
            score=score,
            avg_approval=p.avg_approval,
            gdp_growth_avg=p.gdp_growth_avg,
        ))

    w = PRESIDENT_SCORE_WEIGHTS
    entries.sort(
        key=lambda e: (
            e.score.independence * w.get("independence", 0.15)
            + e.score.follow_through * w.get("followThrough", 0.20)
            + e.score.public_mandate * w.get("publicMandate", 0.15)
            + e.score.effectiveness * w.get("effectiveness", 0.20)
            + e.score.competence * w.get("competence", 0.15)
            + e.score.agency_alignment * w.get("agencyAlignment", 0.15)
        ),
        reverse=True,
    )
    return entries
