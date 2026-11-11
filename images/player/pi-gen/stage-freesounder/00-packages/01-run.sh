#!/bin/bash -e
# Stage the pinned snapcast / go-librespot / AirPlay debs (see images/player/deps).
CACHE="${DEPS_CACHE:-${FREESOUNDER_ROOT}/images/player/.cache/deps}"
# shellcheck source=/dev/null
source "${FREESOUNDER_ROOT}/images/player/deps/versions.env"
DEST="${ROOTFS_DIR}/tmp/fs-debs"
rm -rf "$DEST"
install -d "$DEST"
install -m 0644 "$CACHE/$SNAPSERVER_DEB" "$CACHE/$SNAPCLIENT_DEB" "$DEST/"
if ls "$CACHE/airplay2/"shairport-sync_*.deb >/dev/null 2>&1; then
	install -m 0644 "$CACHE/airplay2/"shairport-sync_*.deb "$CACHE/airplay2/"nqptp_*.deb "$DEST/"
	echo 2 > "$DEST/airplay"
else
	echo 1 > "$DEST/airplay"
fi
echo "$GO_LIBRESPOT_VERSION" > "$DEST/go-librespot-version"
tar -xzf "$CACHE/$GO_LIBRESPOT_TGZ" -C "$DEST" go-librespot
install -m 0755 "$DEST/go-librespot" "${ROOTFS_DIR}/usr/local/bin/go-librespot"
rm -f "$DEST/go-librespot"
