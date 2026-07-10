"""Tests for president_service's live-data disclosure logic."""

from app.models import President
from app.services.president_service import _competence_has_live_data


def _make_president(id_: str, eo_count: int | None) -> President:
    return President(
        id=id_, name="Test", party="D", number=99,
        term_start="2020-01-01", eo_count=eo_count,
    )


class TestCompetenceHasLiveData:
    def test_dynamic_president_with_eo_data_is_live(self):
        p = _make_president("obama-44", eo_count=276)
        assert _competence_has_live_data(p) is True

    def test_dynamic_president_without_eo_data_is_not_live(self):
        p = _make_president("trump-47", eo_count=None)
        assert _competence_has_live_data(p) is False

    def test_non_dynamic_president_is_never_live(self):
        # Historical presidents never have live EO data wired up, even
        # if eo_count happens to be populated from the seed.
        p = _make_president("lincoln-16", eo_count=48)
        assert _competence_has_live_data(p) is False
