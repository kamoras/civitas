"""Shared helpers for annual financial disclosure reports (asset holdings).

A periodic transaction report (ptr_common.py) says what a member *bought or
sold*. The annual Financial Disclosure report says what they *hold*: every
asset worth more than $1,000 (or that produced more than $200 of income) at
the end of the calendar year, owned by the member, their spouse, or a
dependent child, each with its value reported as a statutory *bracket* —
never an exact figure. That is the data behind the holdings breakdown on a
scorecard.

Both chambers report the same statutory content (Ethics in Government Act,
5 U.S.C. §13104) but with a different asset-type vocabulary: the House form
tags each asset with a two-letter code (``[ST]``, ``[MF]``, ...), while the
Senate eFD form prints a category name ("Stocks", "Mutual Funds", ...). Both
vocabularies are mapped here onto one small set of holding categories so a
senator's and a representative's breakdowns are comparable.

That mapping is a translation of the forms' own published vocabulary, not a
classification decision — the filer chose the asset type when they filed,
and the same documented data-format exception that covers
``ptr_common.OWNER_CODES`` covers it (AGENTS.md principle 1). No asset is
ever assigned a category from its *name*: a row whose type is missing or
unrecognized goes to OTHER, and an unrecognized Senate type is logged so the
table can be extended from the real value rather than a guess.
"""

import logging
import re
from dataclasses import dataclass

from app.pipeline.fetch.ptr_common import OPEN_ENDED_AMOUNT_RE, extract_ticker

logger = logging.getLogger(__name__)


@dataclass
class HoldingRow:
    """One asset line from Schedule A (House) / Part 3 (Senate).

    value_low/value_high are None when the form states no bracket at all
    ("Undetermined", "Value not readily ascertainable"). A closed bracket is
    (low, high); an open-ended top bracket ("Over $50,000,000", "Spouse/DC
    Over $1,000,000") is (low, low) — the same encoding
    ``ptr_common.parse_amount_range`` and ``StockTradeSchema.amount_open_ended``
    use, so every consumer renders it as "$X+" rather than a ceiling the
    filing never stated. value_text keeps the cell exactly as printed.
    """
    asset_name: str
    asset_type: str          # raw form value: House code ("ST") or Senate label ("Stocks")
    category: str            # one of HOLDING_CATEGORIES
    owner: str               # self | spouse | joint | dependent
    value_text: str
    value_low: float | None
    value_high: float | None
    account: str | None = None   # the containing account/entity, when the asset is held inside one
    ticker: str | None = None


# Display order is the order here; the frontend receives labels and colors
# from the API rather than hardcoding them (AGENTS.md, config as single
# source of truth). Colors are a fixed categorical order validated for
# colorblind separation and >= 3:1 contrast against the site's dark surface;
# OTHER is deliberately a neutral gray, not a ninth hue. Color follows the
# category, so a member without some category never repaints the rest.
HOLDING_CATEGORIES: dict[str, dict[str, str]] = {
    "STOCKS":      {"label": "Stocks",                "color": "#3987e5"},
    "FUNDS":       {"label": "Mutual funds & ETFs",   "color": "#d95926"},
    "BONDS":       {"label": "Bonds & Treasuries",    "color": "#199e70"},
    "CASH":        {"label": "Bank & cash",           "color": "#c98500"},
    "RETIREMENT":  {"label": "Retirement & pensions", "color": "#d55181"},
    "REAL_ESTATE": {"label": "Real estate",           "color": "#008300"},
    "BUSINESS":    {"label": "Business interests",    "color": "#9085e9"},
    "CRYPTO":      {"label": "Crypto",                "color": "#e66767"},
    "OTHER":       {"label": "Other",                 "color": "#8a857d"},
}

# The House Clerk's published asset-type code list
# (https://fd.house.gov/reference/asset-type-codes.aspx, retrieved 2026-09),
# every code accounted for. Grouping is by what the asset *is* as the filer
# declared it: a pooled vehicle is a fund, an ownership stake in a private
# company is a business interest, and so on.
HOUSE_ASSET_TYPE_CATEGORY: dict[str, str] = {
    "ST": "STOCKS",        # Stocks (including ADRs)
    "PS": "BUSINESS",      # Stock (Not Publicly Traded)
    "RS": "STOCKS",        # Restricted Stock Units (RSUs)
    "SA": "STOCKS",        # Stock Appreciation Right
    "OP": "STOCKS",        # Options
    "MF": "FUNDS",         # Mutual Funds
    "EF": "FUNDS",         # Exchange Traded Funds (ETF)
    "ET": "FUNDS",         # Exchange Traded Notes
    "MA": "FUNDS",         # Managed Accounts (e.g., SMA and UMA)
    "BK": "FUNDS",         # Brokerage Accounts
    "HE": "FUNDS",         # Hedge Funds & Private Equity Funds (EIF)
    "HN": "FUNDS",         # Hedge Funds & Private Equity Funds (non-EIF)
    "IC": "FUNDS",         # Investment Club
    "5F": "FUNDS",         # 529 Portfolio
    "5C": "FUNDS",         # 529 College Savings Plan
    "CS": "BONDS",         # Corporate Securities (Bonds and Notes)
    "GS": "BONDS",         # Government Securities and Agency Debt
    "AB": "BONDS",         # Asset-Backed Securities
    "BA": "CASH",          # Bank Accounts, Money Market Accounts and CDs
    "IH": "CASH",          # IRA (Held in Cash)
    "FE": "CASH",          # Foreign Exchange Position (Currency)
    "4K": "RETIREMENT",    # 401K and Other Non-Federal Retirement Accounts
    "IR": "RETIREMENT",    # IRA
    "DB": "RETIREMENT",    # Defined Benefit Pension
    "PE": "RETIREMENT",    # Pensions
    "FN": "RETIREMENT",    # Fixed Annuity
    "VA": "RETIREMENT",    # Variable Annuity
    "RP": "REAL_ESTATE",   # Real Property
    "RE": "REAL_ESTATE",   # Real Estate Invest. Trust (REIT)
    "RF": "REAL_ESTATE",   # REIT (EIF)
    "RN": "REAL_ESTATE",   # REIT (non-EIF)
    "DS": "REAL_ESTATE",   # Delaware Statutory Trust
    "FA": "REAL_ESTATE",   # Farms
    "OL": "BUSINESS",      # Ownership Interest (Engaged in a Trade or Business)
    "OI": "BUSINESS",      # Ownership Interest (Holding Investments)
    "IP": "BUSINESS",      # Intellectual Property & Royalties
    "MO": "BUSINESS",      # Mineral/Oil/Solar Energy Rights
    "CT": "CRYPTO",        # Cryptocurrency
    "5P": "OTHER",         # 529 Prepaid Tuition Plan
    "CO": "OTHER",         # Collectibles
    "DO": "OTHER",         # Debts Owed to the Filer
    "EQ": "OTHER",         # Excepted/Qualified Blind Trust
    "FU": "OTHER",         # Futures
    "PM": "OTHER",         # Precious Metals
    "TR": "OTHER",         # Trust
    "VI": "OTHER",         # Variable Insurance
    "WU": "OTHER",         # Whole/Universal Insurance
    "OT": "OTHER",         # Other
}

# The Senate eFD form's "Asset Type" values, as printed on filed reports —
# every value that appeared across all 223 senators' electronic annual
# reports on file in 2026-09. The form prints a type and, for most types, a
# subtype beneath it; the subtype table below only overrides where the
# subtype changes what the asset is ("Corporate Securities" covers listed
# stock, private stock and corporate bonds alike). Matched
# case-insensitively on the whole value.
SENATE_ASSET_TYPE_CATEGORY: dict[str, str] = {
    "corporate securities": "OTHER",   # decided by subtype below
    "american depository receipt": "STOCKS",
    "mutual funds": "FUNDS",
    "investment fund": "FUNDS",
    "brokerage/managed account": "FUNDS",
    "education savings plans": "FUNDS",
    "government securities": "BONDS",
    "foreign bonds": "BONDS",
    "equity index-linked note": "BONDS",
    "bank deposit": "CASH",
    "retirement plans": "RETIREMENT",
    "deferred compensation": "RETIREMENT",
    "annuity": "RETIREMENT",
    "real estate": "REAL_ESTATE",
    "farm/ranch": "REAL_ESTATE",
    "business entity": "BUSINESS",
    "intellectual property": "BUSINESS",
    "cryptocurrency": "CRYPTO",
    "accounts receivable": "OTHER",
    "life insurance": "OTHER",
    "trust": "OTHER",
    "ugma/utma": "OTHER",
    "personal property": "OTHER",
    "other": "OTHER",
}

SENATE_ASSET_SUBTYPE_CATEGORY: dict[tuple[str, str], str] = {
    ("corporate securities", "stock"): "STOCKS",
    ("corporate securities", "stock option"): "STOCKS",
    ("corporate securities", "non-public stock"): "BUSINESS",
    ("corporate securities", "corporate bond"): "BONDS",
    ("real estate", "mineral rights"): "BUSINESS",  # as the House's "MO" code
}

_HOUSE_CODE_RE = re.compile(r"\[([0-9A-Z]{2})\]")


def house_category(code: str | None) -> str:
    if not code:
        return "OTHER"
    category = HOUSE_ASSET_TYPE_CATEGORY.get(code.upper())
    if category is None:
        logger.info("Unrecognized House asset-type code %r — filed under OTHER", code)
        return "OTHER"
    return category


def senate_category(asset_type: str | None, subtype: str | None = None) -> str:
    key = " ".join((asset_type or "").split()).lower()
    if not key:
        return "OTHER"
    sub = " ".join((subtype or "").split()).lower()
    if (key, sub) in SENATE_ASSET_SUBTYPE_CATEGORY:
        return SENATE_ASSET_SUBTYPE_CATEGORY[(key, sub)]
    category = SENATE_ASSET_TYPE_CATEGORY.get(key)
    if category is None:
        logger.info("Unrecognized Senate asset type %r — filed under OTHER", asset_type)
        return "OTHER"
    return category


_BRACKET_RE = re.compile(r"\$?\s*([\d,]+)")


def parse_holding_value(text: str) -> tuple[float | None, float | None]:
    """Parse a Value-of-Asset cell into (low, high).

    - "$15,001 - $50,000"              -> (15001, 50000)
    - "Over $50,000,000" / "Spouse/DC Over $1,000,000" -> (floor, floor), open-ended
    - "None (or less than $1,001)"     -> (0, 1000): the Senate's lowest bracket
    - "None"                           -> (0, 0): nothing held at year end
    - "Undetermined", "--", ""         -> (None, None): no bracket stated

    Never guesses: anything that isn't one of these shapes is (None, None).
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return None, None
    lowered = cleaned.lower()
    if lowered.startswith("none"):
        # The Senate's "None (or less than $1,001)" is a real bracket whose
        # ceiling is one dollar under the printed figure.
        figures = _BRACKET_RE.findall(cleaned)
        if "less than" in lowered and figures:
            try:
                return 0.0, float(figures[0].replace(",", "")) - 1
            except ValueError:
                return None, None
        return 0.0, 0.0
    figures = [f for f in _BRACKET_RE.findall(cleaned) if f.replace(",", "")]
    try:
        values = [float(f.replace(",", "")) for f in figures]
    except ValueError:
        return None, None
    if len(values) >= 2 and values[0] <= values[1]:
        return values[0], values[1]
    if len(values) == 1 and OPEN_ENDED_AMOUNT_RE.search(cleaned):
        return values[0], values[0]
    return None, None


def split_account(asset_text: str) -> tuple[str | None, str]:
    """Split "Parent Account ⇒ Asset" into (account, asset).

    The House form nests an asset inside the account or entity that holds
    it with a "⇒" arrow (an IRA, a brokerage account, an LLC). Only the
    innermost asset is the holding; the rest is kept as context.
    """
    parts = [p.strip() for p in asset_text.split("⇒")]
    if len(parts) < 2:
        return None, asset_text.strip()
    account = " ⇒ ".join(p for p in parts[:-1] if p) or None
    return account, parts[-1]


def strip_house_code(asset_text: str) -> tuple[str, str | None]:
    """Remove the trailing "[XX]" asset-type code, returning (name, code)."""
    codes = _HOUSE_CODE_RE.findall(asset_text)
    if not codes:
        return asset_text.strip(), None
    name = _HOUSE_CODE_RE.sub("", asset_text)
    return " ".join(name.split()), codes[-1]


def ticker_for(asset_name: str) -> str | None:
    return extract_ticker(asset_name)
