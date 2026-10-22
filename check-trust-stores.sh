#!/usr/bin/env bash
# Assert that no trust store in our built images holds a distrusted root.
#
#   ./check-trust-stores.sh BACKEND_IMAGE FRONTEND_IMAGE NGINX_IMAGE
#
# Each store is read out of its image and its certificates parsed (by Node's
# X509Certificate, in the frontend image), so the check is of what the image
# will actually trust, not of the Dockerfile's text (which
# backend/tests/test_distrusted_roots.py checks). Prints the emSign subjects
# found per store, for the record, and exits non-zero if any store holds a
# root in DISTRUSTED, if the backend's Chromium policy doesn't distrust it,
# or if Node in the frontend image is not reading the system store.
# check-and-deploy.sh runs it on every build before deploying.
#
# To distrust another root, add its subject CN here and follow AGENTS.md
# "Distrusted roots".
set -euo pipefail

DISTRUSTED=(
  "emSign ECC Root CA - G3" # GHSA-6q48-6c7w-9gw2
)

if [[ $# -ne 3 ]]; then
  echo "usage: $0 BACKEND_IMAGE FRONTEND_IMAGE NGINX_IMAGE" >&2
  exit 2
fi
BACKEND=$1 FRONTEND=$2 NGINX=$3
failed=0

# Reads a PEM bundle on stdin; prints the emSign subjects and every
# distrusted subject it holds, and exits 1 if it holds one.
PARSE='
const { X509Certificate } = require("crypto");
const distrusted = JSON.parse(process.argv[1]);
let pem = "";
process.stdin.on("data", (d) => (pem += d)).on("end", () => {
  const blocks = pem.match(/-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/g) || [];
  const cns = blocks.map((b) => {
    const cn = new X509Certificate(b).subject.split("\n").find((s) => s.startsWith("CN="));
    return cn ? cn.slice(3) : "";
  });
  const emsign = [...new Set(cns.filter((c) => /emsign/i.test(c)))].sort();
  const bad = [...new Set(cns.filter((c) => distrusted.includes(c)))];
  console.log(`  ${blocks.length} certificates; emSign: ${emsign.join(", ") || "(none)"}`);
  if (blocks.length === 0) { console.log("  FAIL: no certificates read"); process.exit(1); }
  if (bad.length) { console.log(`  FAIL: distrusted root present: ${bad.join(", ")}`); process.exit(1); }
  console.log("  OK");
});
'
distrusted_json=$(printf '%s\n' "${DISTRUSTED[@]}" | sed 's/"/\\"/g; s/.*/"&"/' | paste -sd, | sed 's/.*/[&]/')

# check LABEL IMAGE CMD... : run CMD in IMAGE (entrypoint sh) and parse its stdout.
check() {
  local label=$1 image=$2
  shift 2
  echo "$label"
  if ! docker run --rm --entrypoint sh "$image" -c "$*" \
    | docker run -i --rm --entrypoint node "$FRONTEND" -e "$PARSE" "$distrusted_json"; then
    failed=1
  fi
}

check "backend system bundle (/etc/ssl/certs/ca-certificates.crt)" "$BACKEND" \
  'cat /etc/ssl/certs/ca-certificates.crt'
check "backend OpenSSL hashed directory (/etc/ssl/certs/*.pem)" "$BACKEND" \
  'cat /etc/ssl/certs/*.pem'
check "backend certifi (httpx, requests)" "$BACKEND" \
  'cat "$(python -c "import certifi; print(certifi.where())")"'
check "frontend system bundle (/etc/ssl/cert.pem)" "$FRONTEND" \
  'cat /etc/ssl/cert.pem'
check "nginx system bundle (/etc/ssl/cert.pem)" "$NGINX" \
  'cat /etc/ssl/cert.pem'

# Node's own bundled store can't drop a root, so the image must run Node on
# the system store above (--use-openssl-ca). Under it, Node's "default" CA
# list no longer includes the bundled roots.
# Chromium's root store is compiled in; the image distrusts the same roots by
# managed policy, which must name every one of them.
POLICY='
const { X509Certificate } = require("crypto");
const distrusted = JSON.parse(process.argv[1]);
let s = "";
process.stdin.on("data", (d) => (s += d)).on("end", () => {
  const ders = JSON.parse(s).CADistrustedCertificates || [];
  const cns = ders.map((b) => (new X509Certificate(Buffer.from(b, "base64")).subject.split("\n").find((x) => x.startsWith("CN=")) || "").slice(3));
  console.log(`  CADistrustedCertificates: ${cns.join(", ") || "(none)"}`);
  const missing = distrusted.filter((c) => !cns.includes(c));
  if (missing.length) { console.log(`  FAIL: not distrusted: ${missing.join(", ")}`); process.exit(1); }
  console.log("  OK");
});
'
for dir in /etc/opt/chrome_for_testing /etc/chromium; do
  echo "backend Chromium policy ($dir/policies/managed/distrusted-roots.json)"
  if ! docker run --rm --entrypoint cat "$BACKEND" "$dir/policies/managed/distrusted-roots.json" \
    | docker run -i --rm --entrypoint node "$FRONTEND" -e "$POLICY" "$distrusted_json"; then
    failed=1
  fi
done

echo "frontend Node trust (NODE_OPTIONS=--use-openssl-ca, reads /etc/ssl/cert.pem)"
docker run --rm --entrypoint node "$FRONTEND" -e '
const tls = require("tls");
const { X509Certificate } = require("crypto");
const distrusted = JSON.parse(process.argv[1]);
const cn = (p) => (new X509Certificate(p).subject.split("\n").find((s) => s.startsWith("CN=")) || "").slice(3);
const opts = process.env.NODE_OPTIONS || "";
const def = tls.getCACertificates("default").map(cn);
const bundled = tls.getCACertificates("bundled").map(cn).filter((c) => /emsign/i.test(c)).sort();
console.log(`  NODE_OPTIONS=${opts}`);
console.log(`  bundled store (not used): emSign: ${bundled.join(", ")}`);
if (!/(^|\s)--use-openssl-ca(\s|$)/.test(opts)) { console.log("  FAIL: Node is not run with --use-openssl-ca"); process.exit(1); }
const bad = def.filter((c) => distrusted.includes(c));
if (bad.length) { console.log(`  FAIL: Node default store holds ${bad.join(", ")}`); process.exit(1); }
console.log(`  default store: ${def.length} bundled roots in use; OK`);
' "$distrusted_json" || failed=1

exit "$failed"
