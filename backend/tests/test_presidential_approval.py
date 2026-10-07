"""Tests for presidential_approval.py's recent-window filtering."""

from datetime import datetime

from app.pipeline.fetch.presidential_approval import ApprovalPoll, recent_polls


class TestRecentPolls:
    def test_filters_to_window(self):
        polls = [
            ApprovalPoll("01/01/2026", "01/02/2026", 40, 50, 10, None),
            ApprovalPoll("06/01/2026", "06/02/2026", 35, 55, 10, None),
        ]
        recent = recent_polls(polls, days=90, as_of=datetime(2026, 7, 21))
        assert len(recent) == 1
        assert recent[0].start_date == "06/01/2026"

    def test_unparseable_date_excluded(self):
        polls = [ApprovalPoll("not-a-date", "01/02/2026", 40, 50, 10, None)]
        recent = recent_polls(polls, days=90, as_of=datetime(2026, 7, 21))
        assert recent == []


class TestApprovalPageDiscovery:
    """A president sworn in after the slug table was written is found in
    UCSB's own index, by name, rather than typed in."""

    _INDEX = """<ul>
    <li><a href="https://www.presidency.ucsb.edu/statistics/data/jane-q-public-public-approval">Jane Q. Public</a></li>
    <li><a href="https://www.presidency.ucsb.edu/statistics/data/donald-j-trump-2nd-term-public-approval">Donald J. Trump</a></li>
    <li><a href="https://www.presidency.ucsb.edu/statistics/data/joseph-r-biden-public-approval">Joseph R. Biden</a><a href="https://www.presidency.ucsb.edu/statistics/data/joseph-r-biden-public-approval">, Jr.</a></li>
    <li><a href="https://www.presidency.ucsb.edu/statistics/data/donald-j-trump-public-approval">Donald J. Trump</a></li>
    </ul>"""

    def test_index_lists_each_page_once_newest_first(self):
        from app.pipeline.fetch.presidential_approval import parse_approval_index

        assert parse_approval_index(self._INDEX) == [
            ("Jane Q. Public", "jane-q-public-public-approval"),
            ("Donald J. Trump", "donald-j-trump-2nd-term-public-approval"),
            ("Joseph R. Biden", "joseph-r-biden-public-approval"),
            ("Donald J. Trump", "donald-j-trump-public-approval"),
        ]

    async def test_a_later_president_is_found_by_name(self, monkeypatch):
        from types import SimpleNamespace as P

        from app.pipeline.fetch import presidential_approval as pa

        async def fake_fetch(*a, **k):
            return P(status_code=200, text=self._INDEX)

        monkeypatch.setattr(pa, "fetch_with_retry_requests", fake_fetch)
        monkeypatch.setattr(pa, "api_cache_get", lambda *a, **k: None)
        monkeypatch.setattr(pa, "api_cache_set", lambda *a, **k: None)
        presidents = [P(id="trump-47", name="Donald J. Trump", number=47), P(id="public-48", name="Jane Q. Public", number=48)]
        slugs = await pa.approval_slugs(None, presidents)
        assert slugs["public-48"] == "jane-q-public-public-approval"
        assert slugs["trump-47"] == pa.PRESIDENT_APPROVAL_SLUGS["trump-47"]


class TestByPartyColumns:
    """The by-party breakdown is read by its header labels: its order
    differs between pages (and one header spells "Opproving")."""

    @staticmethod
    def _page(header: list[str], row: list[str]) -> str:
        cells = lambda tag, xs: "".join(f"<{tag}>{x}</{tag}>" for x in xs)  # noqa: E731
        return f"<table><tbody><tr>{cells('td', header)}</tr><tr>{cells('td', row)}</tr></tbody></table>"

    def test_democrats_first(self):
        from app.pipeline.fetch.presidential_approval import _parse_approval_table

        polls = _parse_approval_table(self._page(
            ["Start Date", "End Date", "Approving", "Disapproving", "Unsure/NoData", "",
             "Democrats Approving", "Independents Approving", "Republicans Approving"],
            ["01/16/2017", "01/19/2017", "59", "37", "4", "", "95", "61", "14"],
        ))
        assert (polls[0].dem, polls[0].ind, polls[0].rep) == (95, 61, 14)

    def test_republicans_first(self):
        from app.pipeline.fetch.presidential_approval import _parse_approval_table

        polls = _parse_approval_table(self._page(
            ["Start date", "End Date", "Approving", "Disapproving", "Unsure/NoData", "",
             "Republicans Approving", "Independents Approving", "Democrats Opproving", "Source"],
            ["01/21/2025", "01/27/2025", "47", "48", "5", "", "91", "46", "6", "gallup"],
        ))
        assert (polls[0].dem, polls[0].ind, polls[0].rep) == (6, 46, 91)
        assert polls[0].source == "gallup"

    def test_older_pages_label_it_approval(self):
        from app.pipeline.fetch.presidential_approval import _parse_approval_table

        for labels in (["DemocraticApproval", "IndependentApproval", "RepublicanApproval"],
                       ["Democratic Approval", "Independent Approval", "Republican Approval"]):
            polls = _parse_approval_table(self._page(
                ["Start Date", "End Date", "Approving", "Disapproving", "Unsure/NoData", "", *labels],
                ["12/05/1980", "12/08/1980", "34", "55", "11", "", "49", "30", "14"],
            ))
            assert (polls[0].dem, polls[0].ind, polls[0].rep) == (49, 30, 14)

    def test_a_page_without_the_breakdown_reads_none(self):
        from app.pipeline.fetch.presidential_approval import _parse_approval_table, dated_by_party

        polls = _parse_approval_table(self._page(
            ["Start Date", "End Date", "Approving", "Disapproving", "Unsure/NoData"],
            ["01/16/2017", "01/19/2017", "59", "37", "4"],
        ))
        assert polls[0].dem is None and dated_by_party(polls) == []
