#!/bin/bash -e
# Preload the Music Assistant container so a fresh household host works offline
# and does not spend its first minutes on the network pulling ~700 MB.
# The archive is unpacked into Docker by run-music-assistant on first start.
IMAGE_REF=$(tr -d '[:space:]' < "${FREESOUNDER_ROOT}/images/player/pi-gen/stage-freesounder/01-filesystem/files/etc/freesounder/music-assistant-image")
CACHE="${MA_IMAGE_CACHE:-${FREESOUNDER_ROOT}/images/player/.cache/music-assistant}"
TAG=${IMAGE_REF##*:}
ARCHIVE="${CACHE}/music-assistant-${TAG}-arm64.tar"
mkdir -p "${CACHE}/blobs"
python3 "${FREESOUNDER_ROOT}/images/player/tools/fetch-docker-archive.py" \
	"${IMAGE_REF}" "${ARCHIVE}" --platform linux/arm64 --cache "${CACHE}/blobs"
DEST="${ROOTFS_DIR}/var/lib/freesounder/ma-image"
rm -rf "${DEST}"
install -d -m 0755 "${DEST}"
install -m 0644 "${ARCHIVE}" "${DEST}/music-assistant-${TAG}-arm64.tar"
echo "Preloaded ${IMAGE_REF} ($(du -h "${ARCHIVE}" | cut -f1)) into ${DEST}"
