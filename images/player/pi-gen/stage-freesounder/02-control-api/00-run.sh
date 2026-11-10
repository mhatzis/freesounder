#!/bin/bash -e
# Copy the control-api sources from the freesounder monorepo into the image.
# FREESOUNDER_ROOT is set by images/player/build.sh.
SRC="${FREESOUNDER_ROOT}/services/control-api"
if [ ! -d "$SRC/src" ]; then
	echo "control-api sources not found at $SRC" >&2
	exit 1
fi
DEST="${ROOTFS_DIR}/opt/freesounder/control-api"
rm -rf "${DEST}"
mkdir -p "${DEST}"
# Only ship first-party sources and project metadata, not a host virtualenv.
cp -a "${SRC}/pyproject.toml" "${SRC}/README.md" "${DEST}/"
cp -a "${SRC}/src" "${DEST}/"
if [ -f "${SRC}/uv.lock" ]; then
	cp -a "${SRC}/uv.lock" "${DEST}/"
fi
if [ -d "${FREESOUNDER_ROOT}/images/player/pi-gen/wheels" ]; then
	cp -a "${FREESOUNDER_ROOT}/images/player/pi-gen/wheels" "${DEST}/wheels"
fi
# systemd will run as root; keep ownership root later in chroot.
