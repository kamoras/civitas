"""scripts/fetch_occupation_industry.py reads O*NET's title lists under
every name O*NET has given them: 31.0 renamed "Alternate Titles" to "Job
Titles", and the refresh after it stopped at a missing file."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_occupation_industry.py"
_spec = importlib.util.spec_from_file_location("fetch_occupation_industry", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

OCC = {"231011": {"industry": "LEGAL", "workers": 100}}


def _onet(tmp_path: Path, version: str, alternate: tuple[str, str]) -> Path:
    path = tmp_path / f"{version}.zip"
    name, column = alternate
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"db_{version}_text/Occupation Data.txt", "O*NET-SOC Code\tTitle\tDescription\n23-1011.00\tLawyers\tx\n")
        z.writestr(f"db_{version}_text/{name}", f"O*NET-SOC Code\t{column}\tShort Title\n23-1011.00\tAttorney\tn/a\n")
        z.writestr(f"db_{version}_text/Sample of Reported Titles.txt",
                   "O*NET-SOC Code\tReported Job Title\n23-1011.00\tCounsel\n")
    return path


@pytest.mark.parametrize("version,alternate", [
    ("29_3", ("Alternate Titles.txt", "Alternate Title")),
    ("31_0", ("Job Titles.txt", "Job Title")),
])
def test_alternate_titles_are_read_under_either_name(tmp_path, version, alternate):
    mapping = script.titles(_onet(tmp_path, version, alternate), OCC)
    assert mapping == {"LAWYER": "231011", "ATTORNEY": "231011", "COUNSEL": "231011"}


def test_a_database_without_either_name_stops_rather_than_dropping_titles(tmp_path):
    with pytest.raises(SystemExit, match="Alternate Titles.txt or Job Titles.txt"):
        script.titles(_onet(tmp_path, "32_0", ("Other Titles.txt", "Title")), OCC)
