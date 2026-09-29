"""Is requirements.txt's CPU-only torch pin current, and still published?

torch is pinned by wheel URL + sha256 (the CPU-only build; see the comment
above it in requirements.txt), and Dependabot neither updates URL
requirements nor is allowed to touch the plain non-Linux line
(dependabot.yml's `torch` ignore). This script is what stands in for it:
.github/workflows/torch-cpu-watch.yml runs it weekly, and a red run means
act.

It lists download.pytorch.org's CPU index and:
- checks each pinned wheel (by filename and sha256) is still listed there —
  a pulled or re-uploaded wheel would otherwise break the next image build;
- finds the newest stable release with a wheel for BOTH Linux machines
  (aarch64: the Pi; x86_64: CI) for the Python the pins were built for,
  and when it is newer than the pin, prints the three replacement lines,
  hashes included. After pasting them, run `pytest -m slow`: every
  classification threshold is calibrated against this model stack.

Run from the repo (network required):
    python3 backend/scripts/check_torch_cpu_pin.py

Exits 0 when current, 1 when a newer CPU build exists, 2 when the check
itself can't be trusted (unreadable pins, unreachable index, a pinned wheel
no longer listed).
"""

import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

from packaging.requirements import Requirement
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import Version

INDEX = "https://download.pytorch.org/whl/cpu/torch/"
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"
MACHINES = ("aarch64", "x86_64")
_HREF = re.compile(r'href="(?P<url>[^"#]+\.whl)#sha256=(?P<sha>[0-9a-f]{64})"')


class CheckError(Exception):
    """The check can't give a trustworthy answer (exit 2)."""


def _wheel_facts(filename: str) -> tuple[Version, set[str], set[str]] | None:
    """(version, python tags, machines) for a Linux torch wheel, else None."""
    try:
        name, version, _, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return None
    if name != "torch":
        return None
    machines = {m.group(1) for t in tags if (m := re.search(r"linux.*_(aarch64|x86_64)$", t.platform))}
    return version, {t.interpreter for t in tags}, machines


def pinned() -> tuple[Version, str, dict[str, tuple[str, str]]]:
    """(version, python tag, {machine: (wheel filename, sha256)})."""
    plain, wheels = None, {}
    for raw in REQUIREMENTS.read_text().splitlines():
        line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        if canonicalize_name(req.name) != "torch":
            continue
        if req.url:
            filename = unquote(Path(urlparse(req.url).path).name)
            facts = _wheel_facts(filename)
            if not facts or len(facts[2]) != 1:
                raise CheckError(f"can't read the torch wheel URL: {req.url}")
            (machine,) = facts[2]
            wheels[machine] = (filename, req.url.rsplit("#sha256=", 1)[-1])
        else:
            (spec,) = req.specifier
            plain = Version(spec.version)
    if plain is None or set(wheels) != set(MACHINES):
        raise CheckError(f"expected a plain `torch==` line and URL lines for {MACHINES}")
    (py_tag,) = {t for f, _ in wheels.values() for t in _wheel_facts(f)[1]}
    return plain, py_tag, wheels


def published(py_tag: str) -> dict[Version, dict[str, tuple[str, str, str]]]:
    """{public version: {machine: (url, sha256, filename)}} on the CPU index."""
    html = urllib.request.urlopen(INDEX, timeout=60).read().decode()
    found: dict[Version, dict[str, tuple[str, str, str]]] = {}
    for m in _HREF.finditer(html):
        filename = unquote(m["url"].rsplit("/", 1)[-1])
        facts = _wheel_facts(filename)
        if not facts:
            continue
        version, py_tags, machines = facts
        # Everything on this index is a CPU build; the local tag has been
        # `+cpu` but isn't guaranteed, so compare on the public version.
        public = Version(version.public)
        if py_tag not in py_tags or public.is_prerelease or public.is_devrelease:
            continue
        for machine in machines:
            found.setdefault(public, {})[machine] = (m["url"], m["sha"], filename)
    return found


def main() -> int:
    try:
        current, py_tag, wheels = pinned()
        index = published(py_tag)
        listed = {(f, sha) for by in index.values() for _, sha, f in by.values()}
        gone = [f for f, sha in wheels.values() if (f, sha) not in listed]
        if gone:
            raise CheckError(f"pinned wheel no longer listed (or its hash changed) on {INDEX}: {gone}")
    except Exception as exc:  # noqa: BLE001 — any failure means "couldn't check"
        print(f"error: {exc}", file=sys.stderr)
        return 2
    complete = [v for v, by in index.items() if all(m in by for m in MACHINES)]
    latest = max(complete)
    if latest <= current:
        print(f"torch {current} is the newest CPU build ({py_tag}; {', '.join(MACHINES)}).")
        return 0
    print(f"torch {latest} (CPU) is out; requirements.txt pins {current}. Replace the torch lines with:\n")
    for machine in MACHINES:
        url, sha, _ = index[latest][machine]
        print(f'torch @ {url}#sha256={sha} ; sys_platform == "linux" and platform_machine == "{machine}"')
    print(f'torch=={latest} ; sys_platform != "linux"')
    return 1


if __name__ == "__main__":
    sys.exit(main())
