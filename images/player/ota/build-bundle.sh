#!/bin/bash
# Build a signed RAUC bundle from the slot images that make-ab-image.sh wrote.
#
#   build-bundle.sh --images DIR --version V --out FILE.raucb
#                   [--cert signing.cert.pem --key signing.key.pem] [--keyring ca.cert.pem]
#                   [--format verity|plain] [--compatible freesounder-player-arm64]
#
# Signing material defaults to the env (RAUC_SIGNING_CERT / RAUC_SIGNING_KEY: file paths) and then to the
# local dev keys from gen-dev-keys.sh. In CI the GitHub secrets RAUC_SIGNING_KEY_PEM / RAUC_SIGNING_CERT_PEM
# are written to 0600 temp files and passed here. Keys are never printed.
# verity (default) is what players accept; plain only exists for the loop-device simulation (no dm-verity).
set -euo pipefail
DEV_KEYS=${OTA_DEV_KEYS:-$HOME/.config/freesounder-build/ota-dev-keys}
IMAGES="" VERSION="" OUTF="" FORMAT=verity COMPATIBLE=freesounder-player-arm64
CERT=${RAUC_SIGNING_CERT:-$DEV_KEYS/signing.cert.pem}
KEY=${RAUC_SIGNING_KEY:-$DEV_KEYS/signing.key.pem}
KEYRING=${RAUC_KEYRING_PEM:-$DEV_KEYS/ca.cert.pem}
while [[ $# -gt 0 ]]; do
	case "$1" in
		--images) IMAGES=$2; shift ;;
		--version) VERSION=$2; shift ;;
		--out) OUTF=$2; shift ;;
		--cert) CERT=$2; shift ;;
		--key) KEY=$2; shift ;;
		--keyring) KEYRING=$2; shift ;;
		--format) FORMAT=$2; shift ;;
		--compatible) COMPATIBLE=$2; shift ;;
		*) echo "unknown option $1" >&2; exit 2 ;;
	esac
	shift
done
die() { echo "build-bundle: $*" >&2; exit 1; }
[[ -n "$IMAGES" && -n "$VERSION" && -n "$OUTF" ]] || die "--images, --version and --out are required"
[[ -f "$IMAGES/rootfs.ext4" && -f "$IMAGES/boot.vfat" ]] || die "$IMAGES must hold rootfs.ext4 and boot.vfat"
[[ -f "$CERT" && -f "$KEY" ]] || die "signing cert/key not found (run gen-dev-keys.sh, or set RAUC_SIGNING_CERT/KEY)"
[[ "$FORMAT" == verity || "$FORMAT" == plain ]] || die "--format must be verity or plain"
command -v rauc >/dev/null || die "rauc is not installed (apt install rauc)"
command -v mksquashfs >/dev/null || die "mksquashfs is not installed (apt install squashfs-tools)"

WORK=$(mktemp -d "${TMPDIR:-/var/tmp}/fs-bundle.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
mkdir "$WORK/content"
# Hard links where possible (multi-GB images); RAUC reads them, it does not change them.
ln "$IMAGES/rootfs.ext4" "$WORK/content/rootfs.ext4" 2>/dev/null || cp --sparse=always "$IMAGES/rootfs.ext4" "$WORK/content/"
ln "$IMAGES/boot.vfat" "$WORK/content/boot.vfat" 2>/dev/null || cp "$IMAGES/boot.vfat" "$WORK/content/"
for f in rootfs.ext4 boot.vfat; do
	(( $(stat -c %s "$WORK/content/$f") % 4096 == 0 )) || die "$f size is not a multiple of 4096 (needed for adaptive updates)"
done
BUILD=$(git -C "$(dirname "$0")" rev-parse --short HEAD 2>/dev/null || echo unknown)
adaptive=""
[[ "$FORMAT" == verity ]] && adaptive="adaptive=block-hash-index"
cat > "$WORK/content/manifest.raucm" <<EOF
[update]
compatible=$COMPATIBLE
version=$VERSION
description=FreeSounder $VERSION
build=$BUILD

[bundle]
format=$FORMAT

[image.rootfs]
filename=rootfs.ext4
$adaptive

[image.boot]
filename=boot.vfat
$adaptive
EOF
mkdir -p "$(dirname "$OUTF")"
rm -f "$OUTF"
rauc bundle --cert="$CERT" --key="$KEY" \
	--mksquashfs-args="-comp xz -b 1M -no-progress" "$WORK/content" "$OUTF" >/dev/null
# Verify exactly the way a player does (codeSigning purpose, bundle signing time), not with
# OpenSSL's default S/MIME purpose that `rauc bundle --keyring` would use.
cat > "$WORK/verify.conf" <<EOF
[system]
compatible=$COMPATIBLE
bootloader=noop
bundle-formats=plain verity

[keyring]
path=$(realpath "$KEYRING")
check-purpose=codesign
use-bundle-signing-time=true
EOF
rauc --conf="$WORK/verify.conf" info --output-format=shell "$OUTF" > "$OUTF.info" 2>"$WORK/verify.err" \
	|| { cat "$WORK/verify.err" >&2; die "the new bundle does not verify against $KEYRING"; }
(cd "$(dirname "$OUTF")" && sha256sum "$(basename "$OUTF")" > "$(basename "$OUTF").sha256")
echo "bundle: $OUTF ($(du -h "$OUTF" | cut -f1), format $FORMAT, verified against $(basename "$KEYRING"))"
