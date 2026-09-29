"""requirements.txt pins the whole resolved tree, exactly.

The file says so, and dependabot.yml's groups/ignores are reasoned from it,
but nothing enforced it: by 2026-09-29 eleven installed packages (the
cryptography/cffi chain under atproto, pdfminer.six under pdfplumber, ...)
had no pin at all and atproto was a `>=` range, so a rebuild could install a
different tree than the one CI tested. The same audit found torch being
installed as the CUDA build — ~4 GB of NVIDIA libraries in every image for a
Pi with no NVIDIA GPU.

These read the environment CI builds from requirements.txt. The walk starts
from the pinned packages and follows their dependencies, so extra tools in a
developer's own venv don't trip it.
"""

import importlib.metadata as md
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def _pins() -> dict[str, Requirement]:
    pins = {}
    for line in REQUIREMENTS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate():
            continue
        pins[canonicalize_name(req.name)] = req
    return pins


def _installed_tree(roots) -> set[str]:
    seen, stack = set(), list(roots)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            requires = md.requires(name) or []
        except md.PackageNotFoundError:
            continue
        for spec in requires:
            req = Requirement(spec)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            stack.append(canonicalize_name(req.name))
    return seen


def test_every_installed_dependency_is_pinned():
    pins = _pins()
    unpinned = sorted(
        name for name in _installed_tree(pins) - set(pins)
        if _is_installed(name)
    )
    assert not unpinned, f"installed but not pinned in requirements.txt: {unpinned}"


def test_every_pin_is_exact():
    loose = sorted(
        str(req) for req in _pins().values()
        if not req.url and not re.fullmatch(r"==[^,*]+", str(req.specifier))
    )
    assert not loose, f"pins must be exact (== or a hashed URL): {loose}"


def test_torch_is_the_cpu_build():
    import torch

    assert torch.version.cuda is None, (
        f"torch {torch.__version__} is a CUDA build; requirements.txt should "
        "pin the CPU-only wheel"
    )
    cuda_stack = sorted(
        d.metadata["Name"] for d in md.distributions()
        if re.match(r"(nvidia-|cuda-|triton$)", d.metadata["Name"] or "", re.I)
    )
    assert not cuda_stack, f"CUDA packages installed: {cuda_stack}"


def _is_installed(name: str) -> bool:
    try:
        md.distribution(name)
        return True
    except md.PackageNotFoundError:
        return False
