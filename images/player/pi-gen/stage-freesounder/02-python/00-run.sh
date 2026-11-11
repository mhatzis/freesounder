#!/bin/bash -e
# Copy first-party Python services into the image (installed into one venv in
# the chroot step). FREESOUNDER_ROOT is set by images/player/build.sh.
for svc in freesounderd ble-provisioning; do
	SRC="${FREESOUNDER_ROOT}/services/${svc}"
	if [ ! -d "$SRC/src" ]; then
		echo "${svc} sources not found at $SRC" >&2
		exit 1
	fi
	DEST="${ROOTFS_DIR}/opt/freesounder/${svc}"
	rm -rf "${DEST}"
	mkdir -p "${DEST}"
	cp -a "${SRC}/pyproject.toml" "${SRC}/README.md" "${DEST}/"
	cp -a "${SRC}/src" "${DEST}/"
	find "${DEST}" -name __pycache__ -prune -exec rm -rf {} +
done
if [ -d "${FREESOUNDER_ROOT}/images/player/pi-gen/wheels" ]; then
	rm -rf "${ROOTFS_DIR}/opt/freesounder/wheels"
	cp -a "${FREESOUNDER_ROOT}/images/player/pi-gen/wheels" "${ROOTFS_DIR}/opt/freesounder/wheels"
fi
# Live tone/balance ALSA plugin (compiled for the board in 02-run-chroot.sh).
rm -rf "${ROOTFS_DIR}/opt/freesounder/fsdsp"
mkdir -p "${ROOTFS_DIR}/opt/freesounder/fsdsp"
cp -a "${FREESOUNDER_ROOT}/services/fsdsp/fsdsp.c" "${ROOTFS_DIR}/opt/freesounder/fsdsp/"
