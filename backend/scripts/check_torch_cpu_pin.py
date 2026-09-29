"""Is requirements.txt's CPU-only torch pin the newest one published?

torch is pinned by wheel URL + sha256 (the CPU-only build; see the comment
above it in requirements.txt), and Dependabot neither updates URL
requirements nor is allowed to touch the plain non-Linux line
(dependabot.yml's `torch` ignore). This script is what stands in for it:
.github/workflows/torch-cpu-watch.yml runs it weekly, and a newer release
turns that run red.

It lists download.pytorch.org's CPU index, finds the newest stable torch
that has a cp313 manylinux wheel for BOTH aarch64 (the Pi) and x86_64 (CI),
and compares it with the pinned version. When newer, it prints the three
replacement lines, hashes included, so the bump is a paste — then run the
embedding tests (`pytest -m slow`), since every classification threshold
is calibrated against this model stack's output.

Run from the repo (network required):
    python3 backend/scripts/check_torch_cpu_pin.py

Exits 0 when current, 1 when a newer CPU build exists, 2 on error.
"""

import re
import sys
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

INDEX = "https://download.pytorch.org/whl/cpu/torch/"
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"
PY_TAG = "cp313"
MACHINES = ("aarch64", "x86_64")
_WHEEL = re.compile(
    r'href="(?P<url>[^"]*/torch-(?P<version>[^-]+)%2Bcpu-'
    + PY_TAG + "-" + PY_TAG
    + r'-manylinux_[0-9_]+_(?P<machine>aarch64|x86_64)\.whl)#sha256=(?P<sha>[0-9a-f]{64})"'
)


def pinned_version() -> Version:
    for line in REQUIREMENTS.read_text().splitlines():
        line = line.split(" #")[0].strip()
        if line.startswith("torch=="):
            (spec,) = Requirement(line).specifier
            return Version(spec.version)
    raise SystemExit("no plain `torch==` line in requirements.txt")


def published() -> dict[Version, dict[str, tuple[str, str]]]:
    """{version: {machine: (url, sha256)}} for stable cp313 CPU wheels."""
    html = urllib.request.urlopen(INDEX, timeout=60).read().decode()
    found: dict[Version, dict[str, tuple[str, str]]] = {}
    for m in _WHEEL.finditer(html):
        try:
            version = Version(m["version"])
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease:
            continue
        found.setdefault(version, {})[m["machine"]] = (m["url"], m["sha"])
    return found


def main() -> int:
    try:
        current = pinned_version()
        wheels = published()
    except Exception as exc:  # noqa: BLE001 — any failure is "couldn't check"
        print(f"error: {exc}", file=sys.stderr)
        return 2
    complete = [v for v, by in wheels.items() if all(m in by for m in MACHINES)]
    if not complete:
        print(f"error: no {PY_TAG} CPU wheels for {MACHINES} on {INDEX}", file=sys.stderr)
        return 2
    latest = max(complete)
    if latest <= current:
        print(f"torch {current}+cpu is the newest CPU build ({PY_TAG}, {', '.join(MACHINES)}).")
        return 0
    print(f"torch {latest}+cpu is out; requirements.txt pins {current}+cpu. Replace the torch lines with:\n")
    for machine in MACHINES:
        url, sha = wheels[latest][machine]
        print(f'torch @ {url}#sha256={sha} ; sys_platform == "linux" and platform_machine == "{machine}"')
    print(f'torch=={latest} ; sys_platform != "linux"')
    return 1


if __name__ == "__main__":
    sys.exit(main())
