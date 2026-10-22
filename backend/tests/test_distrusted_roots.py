"""Every image we build distrusts the roots listed in check-trust-stores.sh.

The list there is the one source: each root must be removed from the system
store in all three Dockerfiles, named in the backend's Chromium policy, and
Node and Chromium must actually read the cleaned stores. This checks the
Dockerfiles' text; check-trust-stores.sh checks the built images (and
check-and-deploy.sh runs it before every deploy). AGENTS.md "Distrusted
roots" says how to add one.
"""

import base64
import json
import re
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID

from app.pipeline.fetch import senate_ptr

REPO = Path(__file__).resolve().parents[2]
CHECK_SCRIPT = REPO / "check-trust-stores.sh"
BACKEND_DOCKERFILE = REPO / "backend" / "Dockerfile"
FRONTEND_DOCKERFILE = REPO / "frontend" / "Dockerfile"
NGINX_DOCKERFILE = REPO / "nginx" / "Dockerfile"
CHROMIUM_POLICY = REPO / "backend" / "chromium-distrusted-roots.json"


def _distrusted() -> list[str]:
    """The subject CNs in check-trust-stores.sh's DISTRUSTED array."""
    block = re.search(r"^DISTRUSTED=\((.*?)^\)", CHECK_SCRIPT.read_text(), re.M | re.S)
    assert block, "check-trust-stores.sh has no DISTRUSTED=( ... ) array"
    names = re.findall(r'^\s*"([^"]+)"', block.group(1), re.M)
    assert names
    return names


def _distrust_command(cn: str) -> str:
    """The sed that marks the root '!' (deselected) in ca-certificates.conf.
    Debian and Alpine both name the file after the CN, spaces as '_'."""
    return f"s|^mozilla/{cn.replace(' ', '_')}\\.crt$|!&|"


def _runs(text: str) -> list[str]:
    """Each RUN instruction, continuation lines joined."""
    joined = re.sub(r"\\\n", " ", text)
    return [line for line in joined.splitlines() if line.startswith("RUN ")]


def _assert_store_cleaned(text: str, where: str) -> None:
    runs = _runs(text)
    for cn in _distrusted():
        matching = [r for r in runs if _distrust_command(cn) in r]
        assert matching, f"{where} does not distrust {cn!r} (expected {_distrust_command(cn)!r})"
        assert all("update-ca-certificates" in r for r in matching), (
            f"{where}: the store must be rebuilt (update-ca-certificates) in the same RUN"
        )


def test_backend_system_store_distrusts_each_root():
    text = BACKEND_DOCKERFILE.read_text()
    _assert_store_cleaned(text, "backend/Dockerfile")
    # After every apt step: a ca-certificates upgrade would rebuild the store.
    runs = _runs(text)
    distrust = max(i for i, r in enumerate(runs) if "update-ca-certificates" in r)
    installs = [i for i, r in enumerate(runs) if "apt-get install" in r or "--with-deps" in r]
    assert installs and max(installs) < distrust


def test_frontend_runner_distrusts_each_root_and_node_reads_the_system_store():
    runner = FRONTEND_DOCKERFILE.read_text().split("AS runner", 1)
    assert len(runner) == 2, "frontend/Dockerfile has no runner stage"
    _assert_store_cleaned(runner[1], "frontend/Dockerfile runner stage")
    # Node's bundled store can't drop a root: Node must read the system one.
    env = re.search(r"^ENV NODE_OPTIONS=(.*)$", runner[1], re.M)
    assert env and re.search(r"(^|\s)--use-openssl-ca(\s|$)", env.group(1))


def test_nginx_distrusts_each_root():
    _assert_store_cleaned(NGINX_DOCKERFILE.read_text(), "nginx/Dockerfile")


def test_chromium_policy_names_each_root():
    policy = json.loads(CHROMIUM_POLICY.read_text())
    cns = set()
    for der in policy["CADistrustedCertificates"]:
        cert = x509.load_der_x509_certificate(base64.b64decode(der))
        cns.add(cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)
    assert set(_distrusted()) <= cns
    text = BACKEND_DOCKERFILE.read_text()
    assert re.search(
        r"^COPY chromium-distrusted-roots\.json /etc/opt/chrome_for_testing/policies/managed/",
        text,
        re.M,
    ), "Playwright's Chrome for Testing build reads policy only from /etc/opt/chrome_for_testing"


def test_every_chromium_launch_uses_the_full_browser():
    """The headless shell reads no managed policy, so it would trust the
    distrusted roots; every launch must name the full browser."""
    assert senate_ptr.CHROMIUM_CHANNEL == "chromium"
    launches = []
    for path in (REPO / "backend" / "app").rglob("*.py"):
        for call in re.findall(r"chromium\.launch\(([^)]*)\)", path.read_text()):
            launches.append((path.name, call))
    assert launches
    for name, args in launches:
        assert "channel=CHROMIUM_CHANNEL" in args, f"{name}: chromium.launch({args}) uses the headless shell"


def test_deploy_checks_the_built_images():
    deploy = (REPO / "check-and-deploy.sh").read_text()
    assert "./check-trust-stores.sh" in deploy
