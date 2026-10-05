#!/bin/bash
# Explicit application activation/rollback; never performs schema migration.
set -euo pipefail
umask 027
[[ $(id -u) = 0 ]] || { echo 'Run with sudo.' >&2; exit 2; }
[[ $# = 1 ]] || { echo 'Usage: activate-release.sh /srv/aether/releases/RELEASE_ID' >&2; exit 2; }
release=$(realpath -e -- "$1")
[[ $release =~ ^/srv/aether/releases/[a-zA-Z0-9_-]+$ ]] || exit 2
cd "$release"
sha256sum --check --strict --status SHA256SUMS
while IFS=$'\t' read -r reference expected architecture; do
  [[ $(docker image inspect --format '{{.Id}} {{.Architecture}}' "$reference") = "$expected $architecture" ]] || exit 3
done < images.tsv
docker compose --env-file release.env -f compose.yaml config --quiet
# Failed upgrades remain visible; do not reset persisted state or automatically replay requests.
docker compose --env-file release.env -f compose.yaml up -d --pull never --wait --wait-timeout 360
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8080/p3/readyz >/dev/null
previous=$(readlink -e /srv/aether/current || true)
if [[ -n $previous && $previous != "$release" ]]; then
  ln -sfn -- "$previous" /srv/aether/previous.next
  mv -Tf -- /srv/aether/previous.next /srv/aether/previous
fi
ln -sfn -- "$release" /srv/aether/current.next
mv -Tf -- /srv/aether/current.next /srv/aether/current
echo "Local readiness passed for $(basename "$release"). Verify public TLS and authenticated business calls next."
