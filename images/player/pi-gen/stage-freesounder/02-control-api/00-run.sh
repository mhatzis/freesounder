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

# Player agent (installed into the same venv in the chroot step).
AGENT_SRC="${FREESOUNDER_ROOT}/services/player-agent"
if [ ! -d "$AGENT_SRC/src" ]; then
	echo "player-agent sources not found at $AGENT_SRC" >&2
	exit 1
fi
AGENT_DEST="${ROOTFS_DIR}/opt/freesounder/player-agent"
rm -rf "${AGENT_DEST}"
mkdir -p "${AGENT_DEST}"
cp -a "${AGENT_SRC}/pyproject.toml" "${AGENT_SRC}/README.md" "${AGENT_DEST}/"
cp -a "${AGENT_SRC}/src" "${AGENT_DEST}/"

# BLE Improv provisioning service (installed into the same venv in the chroot step).
BLE_SRC="${FREESOUNDER_ROOT}/services/ble-provisioning"
if [ ! -d "$BLE_SRC/src" ]; then
	echo "ble-provisioning sources not found at $BLE_SRC" >&2
	exit 1
fi
BLE_DEST="${ROOTFS_DIR}/opt/freesounder/ble-provisioning"
rm -rf "${BLE_DEST}"
mkdir -p "${BLE_DEST}"
cp -a "${BLE_SRC}/pyproject.toml" "${BLE_SRC}/README.md" "${BLE_DEST}/"
cp -a "${BLE_SRC}/src" "${BLE_DEST}/"
