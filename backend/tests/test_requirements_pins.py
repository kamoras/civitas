"""requirements.txt pins exactly the dependency tree of the direct dependencies.

The file says it pins the whole resolved tree, and dependabot.yml's groups
and ignores are reasoned from that, but nothing enforced it. By 2026-09-29
it had drifted both ways: eleven installed packages (the cryptography/cffi
chain under atproto, pdfminer.six under pdfplumber, ...) had no pin and
atproto was a `>=` range, so a rebuild could install a tree CI never tested;
and 42 pins from the pre-sqlite-vec vector store were still installed into
every image though nothing used them. The same audit found torch installed
as the CUDA build — ~4 GB of NVIDIA libraries for a Pi with no NVIDIA GPU.

The tree is walked from installed package metadata, once per Linux target
(the Pi is aarch64, CI x86_64), with each target's markers, so a dependency
only one architecture pulls in is still checked. Every walk starts from
DIRECT, so extra tools in a developer's own venv don't trip it.

scripts/requirements-research.txt (the research scripts' extras) is held to
the same rules; its tree check runs only where it is installed — the
`Backend — Research deps` CI job — because installing it alongside the app
would let a test pass on a package production doesn't have.
"""

import importlib.metadata as md
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from packaging.markers import Marker, default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

BACKEND = Path(__file__).resolve().parents[1]
REQUIREMENTS = BACKEND / "requirements.txt"
RESEARCH = BACKEND / "scripts" / "requirements-research.txt"
MACHINES = ("aarch64", "x86_64")

# What the backend itself depends on: imported by app/, migrations/, tests/
# or scripts/, or used without an import (uvicorn's server extras, lxml's
# cssselect, the CI tools). Everything else in requirements.txt must be
# pulled in by one of these. Adding a dependency means adding it here too;
# dropping its last use means removing it here, and the test then names
# every pin that went unused with it.
DIRECT = {
    "alembic", "apscheduler", "atproto", "cssselect", "defusedxml",
    "diff-cover", "fastapi", "httptools", "httpx", "lxml", "mcp", "numpy",
    "packaging", "pdfplumber", "pillow", "playwright", "pydantic",
    "pydantic-settings", "pytesseract", "pytest", "pytest-asyncio",
    "pytest-cov", "pyyaml", "requests", "scikit-learn", "scipy",
    "sentence-transformers", "sqlalchemy", "sqlite-vec", "starlette",
    "tokenizers", "torch", "uvicorn", "uvloop", "watchfiles", "xlrd",
}
# The research scripts' own imports beyond DIRECT (openpyxl: pandas loads it
# by name in read_excel for .xlsx).
RESEARCH_DIRECT = {"onnxruntime", "openpyxl", "pandas", "pyreadr", "rdata", "statsmodels"}


def _target(machine: str) -> dict[str, str]:
    return {**default_environment(), "sys_platform": "linux", "platform_machine": machine}


def _lines(path: Path) -> list[str]:
    out = []
    for raw in path.read_text().splitlines():
        # An inline comment needs whitespace before '#'; a URL's '#sha256='
        # fragment has none, so it survives.
        line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _requirements(path: Path) -> list[Requirement]:
    return [Requirement(line) for line in _lines(path) if not line.startswith("-")]


def _pins(path: Path, env: dict) -> set[str]:
    return {
        canonicalize_name(r.name)
        for r in _requirements(path)
        if not r.marker or r.marker.evaluate(env)
    }


def _tree(roots, env: dict) -> set[str]:
    """Every distribution reachable from `roots` on `env`, following extras.
    A dependency that isn't installed here (another architecture's) is
    still reported; its own dependencies can't be read, but its pin is
    checked."""
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
                req.marker.evaluate({**env, "extra": e}) for e in (extras or {""})
            ):
                continue
            stack.append((canonicalize_name(req.name), frozenset(req.extras)))
    return names


def _assert_tree_matches(roots: set[str], pinned_by_machine) -> None:
    for machine in MACHINES:
        env = _target(machine)
        pins = pinned_by_machine(env)
        missing_roots = sorted(r for r in roots if r not in pins)
        assert not missing_roots, (
            f"[{machine}] listed as a direct dependency but not pinned "
            f"(a typo, or a dependency that should be removed from the list): {missing_roots}"
        )
        tree = _tree(roots, env)
        assert not (tree - pins), f"[{machine}] required but not pinned: {sorted(tree - pins)}"
        assert not (pins - tree), (
            f"[{machine}] pinned but nothing in the direct list needs it — "
            f"remove it: {sorted(pins - tree)}"
        )


def _loose_pins(path: Path) -> list[str]:
    loose = []
    for req in _requirements(path):
        if req.url:
            if not re.search(r"#sha256=[0-9a-f]{64}$", req.url):
                loose.append(str(req))
        elif not re.fullmatch(r"==[^=,*][^,*]*", str(req.specifier)):
            loose.append(str(req))
    return loose


@pytest.mark.parametrize("path", [REQUIREMENTS, RESEARCH], ids=lambda p: p.name)
def test_no_option_lines(path):
    """-e / -r / -c / index options would each hide requirements from the
    checks below (and an extra index is merged with PyPI for every package)."""
    options = [line for line in _lines(path) if line.startswith("-")]
    assert not options, f"option lines in {path.name}: {options}"


@pytest.mark.parametrize("path", [REQUIREMENTS, RESEARCH], ids=lambda p: p.name)
def test_every_pin_is_exact(path):
    loose = _loose_pins(path)
    assert not loose, f"{path.name}: pins must be == or a sha256-hashed wheel URL: {loose}"


def test_pins_are_exactly_the_tree_of_the_direct_dependencies():
    _assert_tree_matches(DIRECT, lambda env: _pins(REQUIREMENTS, env))


def test_research_pins_only_add_to_the_app_pins():
    """Resolved with requirements.txt as constraints: the research file adds
    packages, never a second, possibly different pin of an app one."""
    overlap = _pins(REQUIREMENTS, _target("x86_64")) & _pins(RESEARCH, _target("x86_64"))
    assert not overlap, f"pinned in both files: {sorted(overlap)}"


def test_research_pins_are_exactly_the_research_tree():
    try:
        md.distribution("pandas")
    except md.PackageNotFoundError:
        pytest.skip("research dependencies not installed (the Research deps CI job installs them)")
    _assert_tree_matches(
        DIRECT | RESEARCH_DIRECT,
        lambda env: _pins(REQUIREMENTS, env) | _pins(RESEARCH, env),
    )


def test_torch_lines_agree_on_every_platform():
    """The Pi installs the aarch64 line and CI the x86_64 one, so a hand bump
    that misses one is invisible to CI unless checked here, marker or not."""
    torch = [r for r in _requirements(REQUIREMENTS) if canonicalize_name(r.name) == "torch"]
    urls = [r for r in torch if r.url]
    plain = [r for r in torch if not r.url]
    assert len(urls) == 2 and len(plain) == 1, torch

    (plain_version,) = [s.version for s in plain[0].specifier]
    python_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    machines = set()
    for req in urls:
        wheel = unquote(Path(urlparse(req.url).path).name)
        _, version, _, tags = parse_wheel_filename(wheel)
        assert str(version) == f"{plain_version}+cpu", wheel
        assert {t.interpreter for t in tags} == {python_tag}, (
            f"{wheel}: built for {sorted({t.interpreter for t in tags})}, "
            f"but this interpreter (the image's) is {python_tag}"
        )
        (machine,) = {re.search(r"(aarch64|x86_64)$", t.platform).group(1) for t in tags}
        assert req.marker is not None and req.marker.evaluate(_target(machine)), (
            f"{wheel}: marker doesn't select {machine}"
        )
        machines.add(machine)
    assert machines == set(MACHINES)
    assert plain[0].marker == Marker('sys_platform != "linux"')


def test_torch_is_the_cpu_build():
    import torch

    assert torch.version.cuda is None, f"torch {torch.__version__} is a CUDA build"
    for machine in MACHINES:
        cuda_stack = sorted(
            name for name in _tree(DIRECT, _target(machine))
            if re.match(r"(nvidia-|cuda-|triton$)", name)
        )
        assert not cuda_stack, f"[{machine}] CUDA packages in the dependency tree: {cuda_stack}"


def _imported_distributions(*roots: str) -> dict[str, set[str]]:
    """{distribution (or '?module' if none provides it): files importing it}."""
    import ast

    module_to_dist = md.packages_distributions()
    local = {p.stem for p in (BACKEND / "scripts").glob("*.py")} | {"app", "tests", "conftest"}
    found: dict[str, set[str]] = {}
    for root in roots:
        for path in (BACKEND / root).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    modules = [node.module]
                else:
                    continue
                for module in modules:
                    top = module.split(".")[0]
                    if top in sys.stdlib_module_names or top in local:
                        continue
                    for dist in module_to_dist.get(top, [f"?{top}"]):
                        found.setdefault(canonicalize_name(dist), set()).add(
                            str(path.relative_to(BACKEND))
                        )
    return found


def test_every_app_import_is_a_direct_dependency():
    """Importing a package that only arrives transitively works until the
    parent drops it; declare it in DIRECT (and so pin it as a root)."""
    undeclared = {
        dist: sorted(files)[:3]
        for dist, files in _imported_distributions("app", "migrations", "tests").items()
        if dist not in DIRECT
    }
    assert not undeclared, f"imported but not in DIRECT: {undeclared}"


def test_every_script_import_is_declared():
    try:
        md.distribution("pandas")
    except md.PackageNotFoundError:
        pytest.skip("research dependencies not installed (the Research deps CI job installs them)")
    undeclared = {
        dist: sorted(files)[:3]
        for dist, files in _imported_distributions("scripts").items()
        if dist not in DIRECT | RESEARCH_DIRECT
    }
    assert not undeclared, f"scripts import packages no requirements file declares: {undeclared}"
