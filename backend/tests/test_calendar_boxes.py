"""The calendar page's boxes (special_elections.calendar_boxes): each line a
template over the rows the calendar and the specials board serve."""
from app.special_elections import calendar_boxes


def _cal(voter=(), fec=(), closing=()):
    return {"voterDeadlines": list(voter), "fecDeadlines": list(fec),
            "registrationClosing": list(closing), "closingSoonDays": 10}


def _board(upcoming=(), recent=(), last=None, vacancies=()):
    return {"upcoming": list(upcoming), "recent": list(recent), "lastSpecial": last, "vacancies": list(vacancies)}


VOTER = {"stateName": "Missouri", "label": "Mail ballot requests", "sentence": "Mail ballot requests close Oct 21",
         "date": "2026-10-21", "status": "open", "daysLeft": 0}
FEC = {"date": "2026-10-22", "label": "PRE-GENERAL", "status": "upcoming", "daysLeft": 1}


def test_the_next_deadline_is_the_soonest_open_one_with_the_rest_of_its_day_counted():
    other = {**VOTER, "stateName": "Iowa"}
    closed = {**VOTER, "date": "2026-10-01", "status": "closed", "daysLeft": None}
    nxt = calendar_boxes(_cal([closed, VOTER, other], [FEC]), _board())["nextDeadline"]
    assert nxt["title"] == "Mail ballot requests" and nxt["kicker"] == "Missouri"
    assert (nxt["closes"], nxt["daysLeft"]) == ("2026-10-21", 0)
    assert nxt["dek"] == "Mail ballot requests close Oct 21; 1 more deadline that day."


def test_an_fec_report_is_the_next_deadline_when_it_comes_first():
    nxt = calendar_boxes(_cal([{**VOTER, "date": "2026-10-30", "daysLeft": 9}], [FEC]), _board())["nextDeadline"]
    assert nxt["title"] == "Pre-General report" and nxt["dek"] == "Due to the FEC Oct 22, 2026."
    assert calendar_boxes(_cal(), _board())["nextDeadline"] is None


def test_with_no_special_scheduled_the_box_names_the_last_one():
    last = {"seat": "GA-13", "label": "Special runoff", "date": "2026-08-25"}
    special = calendar_boxes(_cal(), _board(recent=[last]))["nextSpecial"]
    assert special["closes"] is None
    assert special["dek"] == ("No special election is on the FEC's calendar; the last was GA-13's special runoff, "
                              "Aug 25, 2026.")
    nxt = {"id": "x", "seat": "TX-18", "label": "Special general", "date": "2026-11-03", "daysLeft": 13}
    special = calendar_boxes(_cal(), _board(upcoming=[nxt]))["nextSpecial"]
    assert (special["title"], special["daysLeft"], special["href"]) == ("TX-18", 13, "#special-x")


def test_vacancies_and_registration_are_counted_from_the_rows_served():
    seats = [{"office": "H"}, {"office": "H"}, {"office": "S"}]
    closing = [{"stateName": "Idaho", "closesOn": "2026-10-23"}]
    boxes = calendar_boxes(_cal(closing=closing), _board(vacancies=seats))
    assert boxes["vacancies"] == {"count": 3, "dek": "2 seats in the House, 1 seat in the Senate, as the House "
                                                     "Clerk and the Senate list them."}
    assert boxes["registration"] == {"count": 1, "days": 10, "dek": "Soonest: Idaho, Oct 23, 2026."}
