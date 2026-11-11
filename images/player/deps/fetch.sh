#!/bin/bash
# Download and verify the pinned third-party debs/binaries for the image.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=versions.env
source "$HERE/versions.env"
CACHE=${DEPS_CACHE:-$HERE/../.cache/deps}
mkdir -p "$CACHE/airplay2"
fetch() {
	local url=$1 file=$2 sha=$3
	if [[ ! -f "$CACHE/$file" ]] || ! echo "$sha  $CACHE/$file" | sha256sum -c --quiet - 2>/dev/null; then
		curl -fsSL -o "$CACHE/$file.part" "$url"
		mv "$CACHE/$file.part" "$CACHE/$file"
	fi
	echo "$sha  $CACHE/$file" | sha256sum -c --quiet -
	echo "ok $file"
}
SNAP=https://github.com/snapcast/snapcast/releases/download/v${SNAPCAST_VERSION}
fetch "$SNAP/$SNAPSERVER_DEB" "$SNAPSERVER_DEB" "$SNAPSERVER_SHA256"
fetch "$SNAP/$SNAPCLIENT_DEB" "$SNAPCLIENT_DEB" "$SNAPCLIENT_SHA256"
fetch "https://github.com/devgianlu/go-librespot/releases/download/v${GO_LIBRESPOT_VERSION}/$GO_LIBRESPOT_TGZ" \
	"$GO_LIBRESPOT_TGZ" "$GO_LIBRESPOT_SHA256"
if [[ -n "${AIRPLAY2_RUN_ID:-}" ]] && ! ls "$CACHE/airplay2/"shairport-sync_*.deb >/dev/null 2>&1; then
	gh run download "$AIRPLAY2_RUN_ID" -n airplay2-trixie-arm64 -D "$CACHE/airplay2"
fi
ls -l "$CACHE" "$CACHE/airplay2"
