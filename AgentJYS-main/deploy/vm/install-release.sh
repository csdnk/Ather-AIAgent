#!/bin/bash
# Load a verified offline release. Never creates databases or resets schemas.
set -euo pipefail
umask 027
[[ $(id -u) = 0 ]] || { echo 'Run with sudo.' >&2; exit 2; }
[[ $# = 1 ]] || { echo 'Usage: install-release.sh /srv/aether/releases/RELEASE_ID' >&2; exit 2; }
release=$(realpath -e -- "$1")
[[ $release =~ ^/srv/aether/releases/[a-zA-Z0-9_-]+$ ]] || exit 2
cd "$release"
for file in SHA256SUMS images.tsv images.tar release.env compose.yaml Caddyfile activate-release.sh; do
  [[ -f $file && ! -L $file ]] || { echo "Missing release file: $file" >&2; exit 2; }
done
# The authenticated release manifest only permits paths inside this directory.
awk 'NF != 2 || $1 !~ /^[a-f0-9]+$/ || length($1) != 64 || $2 ~ /^\// || $2 ~ /(^|\/)\.\.(\/|$)/ {exit 1}' SHA256SUMS
sha256sum --check --strict --status SHA256SUMS
docker compose version >/dev/null
docker load --input images.tar
while IFS=$'\t' read -r reference expected architecture; do
  [[ $reference =~ ^[a-z0-9./_-]+:[a-zA-Z0-9_.-]+$ && $expected =~ ^sha256:[a-f0-9]{64}$ && $architecture = amd64 ]] || exit 2
  actual=$(docker image inspect --format '{{.Id}} {{.Architecture}}' "$reference")
  [[ $actual = "$expected $architecture" ]] || { echo "Image identity mismatch: $reference" >&2; exit 3; }
done < images.tsv
# This validates interpolation without printing the credential-bearing config.
docker compose --env-file release.env -f compose.yaml config --quiet
echo "Offline images verified for $(basename "$release"). Service activation is a separate step."
