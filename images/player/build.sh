#!/bin/bash
# Build the freesounder Raspberry Pi OS Lite (64-bit) player image with pi-gen.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
PI_GEN_REF=${PI_GEN_REF:-arm64}
PI_GEN_URL=${PI_GEN_URL:-https://github.com/RPi-Distro/pi-gen.git}
WORK_ROOT=${WORK_ROOT:-/workspace/freesounder-build}
PI_GEN_DIR=${PI_GEN_DIR:-$WORK_ROOT/pi-gen}
DEPLOY_DIR=${DEPLOY_DIR:-$WORK_ROOT/deploy}
export FREESOUNDER_ROOT=$REPO_ROOT

export PATH="/usr/local/sbin:/usr/sbin:/sbin:${PATH}"

if [[ "$(id -u)" -ne 0 ]]; then
	echo "Re-running under sudo..."
	exec sudo --preserve-env=FREESOUNDER_ROOT,PI_GEN_REF,PI_GEN_URL,WORK_ROOT,PI_GEN_DIR,DEPLOY_DIR,PATH \
		"$0" "$@"
fi

mkdir -p "$WORK_ROOT" "$DEPLOY_DIR"

if [[ ! -d "$PI_GEN_DIR/.git" ]]; then
	echo "Cloning pi-gen (${PI_GEN_REF})..."
	git clone --depth 1 --branch "$PI_GEN_REF" "$PI_GEN_URL" "$PI_GEN_DIR"
else
	echo "Using existing pi-gen checkout at $PI_GEN_DIR"
fi

# Lite only: skip desktop stages, then run our custom stage and export once.
touch "$PI_GEN_DIR/stage3/SKIP" "$PI_GEN_DIR/stage4/SKIP" "$PI_GEN_DIR/stage5/SKIP"
touch "$PI_GEN_DIR/stage2/SKIP_IMAGES" "$PI_GEN_DIR/stage4/SKIP_IMAGES" "$PI_GEN_DIR/stage5/SKIP_IMAGES"

# This build host (and some CI networks) only reach Raspberry Pi / Debian
# apt mirrors over HTTPS. pi-gen's defaults are HTTP.
sed -i 's|http://deb.debian.org/debian/|https://deb.debian.org/debian/|g' \
	"$PI_GEN_DIR/stage0/prerun.sh" \
	"$PI_GEN_DIR/stage0/00-configure-apt/files/debian.sources"
sed -i 's|http://deb.debian.org/debian-security/|https://deb.debian.org/debian-security/|g' \
	"$PI_GEN_DIR/stage0/00-configure-apt/files/debian.sources"
sed -i 's|http://archive.raspberrypi.com/debian/|https://archive.raspberrypi.com/debian/|g' \
	"$PI_GEN_DIR/stage0/00-configure-apt/files/raspi.sources"


# On some build networks apt SRV lookups for deb.debian.org hit a broken
# intercept (TLS EOF at 198.18.0.1). Disable SRV records in the chroot for
# the duration of the build; stage-freesounder removes the file so it is not shipped.
install -m 644 "$SCRIPT_DIR/pi-gen/patches/99-freesounder-build-apt" \
	"$PI_GEN_DIR/stage0/00-configure-apt/files/99-freesounder-build-apt"
if ! grep -q "99-freesounder-build-apt" "$PI_GEN_DIR/stage0/00-configure-apt/00-run.sh"; then
	python3 - "$PI_GEN_DIR/stage0/00-configure-apt/00-run.sh" << "PY"
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
needle = 'if [ -n "$APT_PROXY" ]; then'
insert = 'install -m 644 files/99-freesounder-build-apt "${ROOTFS_DIR}/etc/apt/apt.conf.d/99-freesounder-build-apt"\n\n'
if needle not in text:
    raise SystemExit(f"could not patch {p}: needle missing")
p.write_text(text.replace(needle, insert + needle, 1))
PY
fi

# When the build host kernel has loop.max_part=0 (no loop0p1 nodes, no
# usable device-mapper), patch pi-gen export to use offset losetup.
python3 "$SCRIPT_DIR/pi-gen/patches/export-prerun-offset-loop.sed.py" \
	"$PI_GEN_DIR/export-image/prerun.sh"
python3 "$SCRIPT_DIR/pi-gen/patches/unmount-image-all-loops.sed.py" \
	"$PI_GEN_DIR/scripts/common"
python3 "$SCRIPT_DIR/pi-gen/patches/export-prerun-vfat-bind.sed.py" \
	"$PI_GEN_DIR/export-image/prerun.sh"
python3 "$SCRIPT_DIR/pi-gen/patches/export-prerun-fat-geometry.sed.py" \
	"$PI_GEN_DIR/export-image/prerun.sh"
python3 "$SCRIPT_DIR/pi-gen/patches/export-finalise-mtools.sed.py" \
	"$PI_GEN_DIR/export-image/05-finalise/01-run.sh"
python3 "$SCRIPT_DIR/pi-gen/patches/export-finalise-unsigned-hash.sed.py" \
	"$PI_GEN_DIR/export-image/05-finalise/01-run.sh"
install -m 0755 "$SCRIPT_DIR/pi-gen/patches/flush-bootfs-mtools.sh" \
	"$PI_GEN_DIR/export-image/flush-bootfs-mtools.sh"
export FLUSH_BOOTFS_MTOOLS="$PI_GEN_DIR/export-image/flush-bootfs-mtools.sh"
install -m 0755 "$SCRIPT_DIR/pi-gen/patches/rehash-rootfs-unsigned.sh" \
	"$PI_GEN_DIR/export-image/05-finalise/rehash-rootfs-unsigned.sh"
export REHASH_ROOTFS_UNSIGNED="$PI_GEN_DIR/export-image/05-finalise/rehash-rootfs-unsigned.sh"
export PATH="/usr/local/sbin:/usr/sbin:/sbin:${PATH}"


# Copy our config in as the default plus an explicit -c argument.
cp "$SCRIPT_DIR/pi-gen/config" "$PI_GEN_DIR/config.freesounder"

STAGE_FS="$SCRIPT_DIR/pi-gen/stage-freesounder"
export WORK_DIR=${WORK_DIR:-$WORK_ROOT/work}
export DEPLOY_DIR
export STAGE_LIST="stage0 stage1 stage2 ${STAGE_FS}"

# Register qemu-aarch64 with binfmt if systemd-binfmt is not running (containers).
if [[ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]]; then
	mkdir -p /proc/sys/fs/binfmt_misc
	mountpoint -q /proc/sys/fs/binfmt_misc || mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc || true
	if [[ -f /usr/lib/binfmt.d/qemu-aarch64.conf ]]; then
		line=$(grep -v '^#' /usr/lib/binfmt.d/qemu-aarch64.conf | head -1)
		echo "$line" > /proc/sys/fs/binfmt_misc/register || true
	fi
fi
if ! arch-test arm64; then
	echo "ERROR: this kernel cannot run aarch64 binaries (binfmt/qemu)." >&2
	echo "Install qemu-user-static / qemu-user-binfmt and register binfmt." >&2
	exit 1
fi

# Pre-fetch aarch64 wheels so the chroot does not compile native extensions under qemu.
WHEEL_DIR="$SCRIPT_DIR/pi-gen/wheels"
if [[ ! -d "$WHEEL_DIR" ]] || [[ -z "$(ls -A "$WHEEL_DIR" 2>/dev/null || true)" ]]; then
	if command -v uv >/dev/null 2>&1; then
		echo "Downloading aarch64 Python wheels..."
		mkdir -p "$WHEEL_DIR"
		tmp_req=$(mktemp)
		(cd "$REPO_ROOT/services/control-api" && uv export --frozen --no-dev --no-hashes) 			| awk 'BEGIN{skip=0} /^(-e |#)|file:\/\// {next} /^    #/ {next} NF{print}' 			> "$tmp_req" || true
		uv run --directory "$REPO_ROOT/services/control-api" --with pip python -m pip download 			-r "$tmp_req" --python-version 313 --platform manylinux2014_aarch64 			--only-binary=:all: --implementation cp --abi cp313 			-d "$WHEEL_DIR" || echo "Wheel download failed; chroot pip will try PyPI."
		rm -f "$tmp_req"
		# Build backend (hatchling) is required for an offline pip install of the project.
		uv run --directory "$REPO_ROOT/services/control-api" --with pip python -m pip download \
			hatchling packaging pathspec pluggy trove-classifiers editables tomlkit \
			--python-version 313 --only-binary=:all: -d "$WHEEL_DIR" \
			|| echo "hatchling wheel download failed; chroot pip will try PyPI."

	fi
fi

if command -v uv >/dev/null 2>&1 && ! compgen -G "$WHEEL_DIR/hatchling-*.whl" > /dev/null; then
	mkdir -p "$WHEEL_DIR"
	echo "Downloading hatchling build-backend wheels..."
	uv run --directory "$REPO_ROOT/services/control-api" --with pip python -m pip download \
		hatchling packaging pathspec pluggy trove-classifiers editables tomlkit \
		--python-version 313 --only-binary=:all: -d "$WHEEL_DIR" \
		|| echo "hatchling wheel download failed; chroot pip will try PyPI."
fi

# dbus-next is a pure-Python dep of ble-provisioning; fetch it when the wheel
# cache already exists from a previous control-api download.
if command -v uv >/dev/null 2>&1 && ! compgen -G "$WHEEL_DIR/dbus_next-*.whl" > /dev/null; then
	mkdir -p "$WHEEL_DIR"
	echo "Downloading dbus-next wheel for ble-provisioning..."
	uv run --directory "$REPO_ROOT/services/ble-provisioning" --with pip python -m pip download \
		dbus-next --python-version 313 --only-binary=:all: -d "$WHEEL_DIR" \
		|| echo "dbus-next wheel download failed; chroot pip will try PyPI."
fi

echo "Starting pi-gen (this takes a good while)..."

cd "$PI_GEN_DIR"
# Prefer a non-docker build; it is what works on this kind of host.
./build.sh -c "$PI_GEN_DIR/config.freesounder"

echo "Build finished. Images in $DEPLOY_DIR :"
ls -lh "$DEPLOY_DIR"
