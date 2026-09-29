"""requirements.txt pins exactly the dependency tree of the direct dependencies.

The file says it pins the whole resolved tree, and dependabot.yml's groups
and ignores are reasoned from that, but nothing enforced it. By 2026-09-29
it had drifted both ways: eleven installed packages (the cryptography/cffi
chain under atproto, pdfminer.six under pdfplumber, ...) had no pin and
atproto was a `>=` range, so a rebuild could install a tree CI never tested;
and 42 pins from the pre-sqlite-vec vector store were still installed into
every image though nothing used them. The same audit found torch installed
as the CUDA build — ~4 GB of NVIDIA libraries for a Pi with no NVIDIA GPU.

These read the environment CI builds from requirements.txt. Every walk
starts from the pins, so extra tools in a developer's own venv don't trip it.
"""

import importlib.metadata as md
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"

# What the backend itself depends on: imported by app/, migrations/, tests/
# or scripts/, or used without an import (uvicorn's server extras, lxml's
# cssselect, the CI tools). Everything else in requirements.txt must be
# pulled in by one of these. Adding a dependency means adding it here too;
# dropping its last use means removing it here, and the test then names
# every pin that went unused with it.
DIRECT = {
    "alembic", "apscheduler", "atproto", "cssselect", "defusedxml",
    "diff-cover", "fastapi", "httptools", "httpx", "lxml", "numpy",
    "packaging", "pdfplumber", "pillow", "playwright", "pydantic",
    "pydantic-settings", "pytesseract", "pytest", "pytest-asyncio",
    "pytest-cov", "pyyaml", "requests", "scikit-learn", "scipy",
    "sentence-transformers", "sqlalchemy", "sqlite-vec", "starlette",
    "tokenizers", "torch", "uvicorn", "uvloop", "watchfiles", "xlrd",
}


def _lines() -> list[str]:
    out = []
    for raw in REQUIREMENTS.read_text().splitlines():
        # An inline comment needs whitespace before '#'; a URL's '#sha256='
        # fragment has none, so it survives.
        line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _all_requirements() -> list[Requirement]:
    return [Requirement(line) for line in _lines() if not line.startswith("-")]


def _pins() -> dict[str, Requirement]:
    """The requirements that apply on this machine, by canonical name."""
    return {
        canonicalize_name(r.name): r
        for r in _all_requirements()
        if not r.marker or r.marker.evaluate()
    }


def _tree(roots) -> set[str]:
    """Every distribution reachable from `roots`, following extras."""
    seen: set[tuple[str, frozenset]] = set()
    names: set[str] = set()
    stack = [(canonicalize_name(r), frozenset()) for r in roots]
    while stack:
        name, extras = stack.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        names.add(name)
        try:
            requires = md.requires(name) or []
        except md.PackageNotFoundError:
            continue
        for spec in requires:
            req = Requirement(spec)
            if req.marker and not any(
                req.marker.evaluate({"extra": e}) for e in (extras or {""})
            ):
                continue
            stack.append((canonicalize_name(req.name), frozenset(req.extras)))
    return names


def test_no_option_lines():
    """-e / -r / -c / index options would each hide requirements from the
    checks below (and an extra index is merged with PyPI for every package)."""
    options = [line for line in _lines() if line.startswith("-")]
    assert not options, f"option lines in requirements.txt: {options}"


def test_pins_are_exactly_the_tree_of_the_direct_dependencies():
    pins = set(_pins())
    tree = _tree(DIRECT)
    assert not (tree - pins), f"installed but not pinned: {sorted(tree - pins)}"
    assert not (pins - tree), (
        "pinned but nothing in DIRECT needs it — remove it from "
        f"requirements.txt: {sorted(pins - tree)}"
    )


def test_every_pin_is_exact():
    loose = []
    for req in _all_requirements():
        if req.url:
            if not re.search(r"#sha256=[0-9a-f]{64}$", req.url):
                loose.append(str(req))
        elif not re.fullmatch(r"==[^=,*][^,*]*", str(req.specifier)):
            loose.append(str(req))
    assert not loose, f"pins must be == or a sha256-hashed wheel URL: {loose}"


def test_torch_lines_agree_on_every_platform():
    """The Pi installs the aarch64 line and CI the x86_64 one, so a hand bump
    that misses one is invisible to CI unless checked here, marker or not."""
    torch = [r for r in _all_requirements() if canonicalize_name(r.name) == "torch"]
    urls = [r for r in torch if r.url]
    plain = [r for r in torch if not r.url]
    assert len(urls) == 2 and len(plain) == 1, torch

    (plain_version,) = [s.version for s in plain[0].specifier]
    machines = set()
    for req in urls:
        wheel = unquote(Path(urlparse(req.url).path).name)
        _, version, _, tags = parse_wheel_filename(wheel)
        assert str(version) == f"{plain_version}+cpu", wheel
        (machine,) = {re.search(r"(aarch64|x86_64)$", t.platform).group(1) for t in tags}
        assert req.marker is not None and req.marker.evaluate(
            {"sys_platform": "linux", "platform_machine": machine}
        ), f"{wheel}: marker doesn't select {machine}"
        machines.add(machine)
    assert machines == {"aarch64", "x86_64"}
    assert plain[0].marker == Marker('sys_platform != "linux"')


def test_torch_is_the_cpu_build():
    import torch

    assert torch.version.cuda is None, f"torch {torch.__version__} is a CUDA build"
    cuda_stack = sorted(
        name for name in _tree(DIRECT)
        if re.match(r"(nvidia-|cuda-|triton$)", name)
    )
    assert not cuda_stack, f"CUDA packages in the dependency tree: {cuda_stack}"
