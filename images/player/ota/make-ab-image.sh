#!/bin/bash
# Turn a pi-gen FreeSounder image (2 partitions: bootfs + rootfs) into the A/B OTA layout.
#
#   make-ab-image.sh <pi-gen.img[.xz]> <out-dir> [version]
#
# Writes to <out-dir>:
#   freesounder-<version>-ab.img(.xz) + .sha256   flash this (root A filled, root B empty, p7 on 1st boot)
#   boot.vfat, rootfs.ext4                        the slot images; build-bundle.sh turns them into a bundle
#   layout.txt                                    sfdisk dump of the result
#
# Needs root (ext4 loop mount for /etc/fstab), e2fsprogs, dosfstools, mtools, fdisk, xz.
# FS_NO_XZ=1 skips compression (tests).
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=layout.env
. "$HERE/layout.env"
export MTOOLS_SKIP_CHECK=1 PATH="/usr/local/sbin:/usr/sbin:/sbin:$PATH"

SRC=${1:?pi-gen image}
OUT=${2:?output dir}
VERSION=${3:-dev}
MIB=$((1024 * 1024))
DISK_HEX=${FS_DISK_ID#0x}

die() { echo "make-ab-image: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || die "run as root"
for t in sfdisk mkfs.vfat mcopy mtype e2fsck resize2fs mount losetup; do
	command -v "$t" >/dev/null || die "missing $t"
done

mkdir -p "$OUT"
WORK=$(mktemp -d "${TMPDIR:-/var/tmp}/fs-ab.XXXXXX")
MNT="$WORK/mnt"
cleanup() {
	mountpoint -q "$MNT" 2>/dev/null && umount "$MNT"
	rm -rf "$WORK"
}
trap cleanup EXIT

if [[ "$SRC" == *.xz ]]; then
	echo "Decompressing $SRC ..."
	xz -dc -T0 "$SRC" > "$WORK/src.img"
	SRC="$WORK/src.img"
fi

# ------------------------------------------------------------------ read the pi-gen image
read -r BOOT_START BOOT_SECTORS < <(sfdisk -d "$SRC" | awk -F'[=,]' '/start=/{n++; if(n==1){gsub(/ /,""); print $2, $4}}')
read -r ROOT_START ROOT_SECTORS < <(sfdisk -d "$SRC" | awk -F'[=,]' '/start=/{n++; if(n==2){gsub(/ /,""); print $2, $4}}')
[[ -n "${BOOT_START:-}" && -n "${ROOT_START:-}" ]] || die "$SRC is not a 2-partition pi-gen image"

echo "Extracting bootfs and rootfs ..."
mkdir -p "$WORK/boot"
mcopy -s -p -m -n -i "$SRC@@$((BOOT_START * 512))" "::/*" "$WORK/boot/"
[[ -f "$WORK/boot/cmdline.txt" && -f "$WORK/boot/config.txt" ]] || die "bootfs has no cmdline.txt/config.txt"
dd if="$SRC" of="$WORK/rootfs.ext4" bs=1M iflag=skip_bytes,count_bytes \
	skip=$((ROOT_START * 512)) count=$((ROOT_SECTORS * 512)) status=none

# ------------------------------------------------------------------ boot slot image
orig_cmdline=$(tr -d '\n' < "$WORK/boot/cmdline.txt")
cmdline_for() { # slot rootpart bootpart
	local args="" tok
	for tok in $orig_cmdline; do
		case "$tok" in
			root=*|rootfstype=*|rootwait|resize|init=*|rauc.slot=*|fsboot=*|rw|ro|quiet) ;;
			*) args+="$tok " ;;
		esac
	done
	printf 'root=PARTUUID=%s-%02d rootfstype=ext4 rw rootwait rauc.slot=%s fsboot=PARTUUID=%s-%02d %s\n' \
		"$DISK_HEX" "$2" "$1" "$DISK_HEX" "$3" "${args% }"
}
cmdline_for A 5 2 > "$WORK/boot/cmdline_a.txt"
cmdline_for B 6 3 > "$WORK/boot/cmdline_b.txt"
rm -f "$WORK/boot/cmdline.txt"
# Each slot's firmware picks its own command line ([boot_partition=N] filter), so the same boot image
# works in either slot. Kept above the DAC block, which apply-dac-overlay rewrites at the end.
python3 - "$WORK/boot/config.txt" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
s = re.sub(r"\n?# --- freesounder-ab-start ---.*?# --- freesounder-ab-end ---\n", "\n", s, flags=re.S)
block = ("# --- freesounder-ab-start ---\n"
         "# A/B OTA: each boot slot uses the command line for its own root slot.\n"
         "[boot_partition=2]\ncmdline=cmdline_a.txt\n[boot_partition=3]\ncmdline=cmdline_b.txt\n[all]\n"
         "# --- freesounder-ab-end ---\n")
m = re.search(r"^# --- freesounder-dac-start ---$", s, flags=re.M)
s = (s[:m.start()] + block + "\n" + s[m.start():]) if m else (s.rstrip("\n") + "\n\n" + block)
open(p, "w").write(s)
PY
truncate -s $((FS_BOOT_MIB * MIB)) "$WORK/boot.vfat"
mkfs.vfat -F 32 -s 1 -n FSBOOT "$WORK/boot.vfat" >/dev/null
mcopy -s -p -m -i "$WORK/boot.vfat" "$WORK/boot"/* ::/

# ------------------------------------------------------------------ bootsel (p1)
truncate -s $((FS_BOOTSEL_MIB * MIB)) "$WORK/bootsel.vfat"
mkfs.vfat -F 16 -n FSBOOTSEL "$WORK/bootsel.vfat" >/dev/null
python3 - "$WORK/autoboot.txt" <<PY
import sys
sys.path.insert(0, "$HERE/../pi-gen/stage-freesounder/01-filesystem/files/usr/lib/freesounder/ota")
import fsota
open(sys.argv[1], "w").write(fsota.render_autoboot("A"))
PY
: > "$WORK/bootsel-config.txt"
mcopy -i "$WORK/bootsel.vfat" "$WORK/autoboot.txt" ::/autoboot.txt
# Pi 3 / Zero 2 W boot ROM loads bootcode.bin only from the first FAT partition (not redundant;
# it practically never changes). An empty config.txt there is needed on Pi 3 per the RPi forum.
mcopy -i "$WORK/bootsel.vfat" "$WORK/boot/bootcode.bin" ::/bootcode.bin
mcopy -i "$WORK/bootsel.vfat" "$WORK/bootsel-config.txt" ::/config.txt

# ------------------------------------------------------------------ root slot image
e2fsck -fy "$WORK/rootfs.ext4" >/dev/null 2>&1 || [[ $? -le 1 ]] || die "e2fsck failed on rootfs"
mkdir -p "$MNT"
mount -o loop "$WORK/rootfs.ext4" "$MNT"
cat > "$MNT/etc/fstab" <<'EOF'
proc            /proc           proc    defaults          0       0
# A/B OTA layout. Root is the slot the firmware booted (root=PARTUUID=46534f54-05|06, rw, on the
# kernel command line). /boot/firmware (this slot's own boot partition) and /data are mounted by
# freesounder-data.service. Nothing here may name a slot.
EOF
find "$MNT/boot/firmware" -mindepth 1 -delete 2>/dev/null || true
mkdir -p "$MNT/data"
missing=()
for f in /usr/bin/rauc /etc/rauc/system.conf /etc/rauc/keyring.pem /usr/lib/freesounder/ota/fsota.py \
	/etc/systemd/system/freesounder-data.service; do
	[[ -e "$MNT$f" ]] || missing+=("$f")
done
if ((${#missing[@]})); then
	echo "WARNING: rootfs lacks OTA files (${missing[*]}); was the image built from feat/ota?" >&2
	[[ "${FS_ALLOW_NO_OTA:-0}" == 1 ]] || { umount "$MNT"; die "refusing (FS_ALLOW_NO_OTA=1 to override)"; }
fi
umount "$MNT"
e2fsck -fy "$WORK/rootfs.ext4" >/dev/null 2>&1 || [[ $? -le 1 ]] || die "e2fsck failed after edit"
ROOTFS_BYTES=$(stat -c %s "$WORK/rootfs.ext4")
((ROOTFS_BYTES <= FS_ROOT_MIB * MIB)) || die "rootfs ($ROOTFS_BYTES bytes) does not fit a ${FS_ROOT_MIB} MiB slot"

# ------------------------------------------------------------------ assemble the disk
A=$FS_ALIGN_MIB
P1=$A
P2=$((P1 + FS_BOOTSEL_MIB))
P3=$((P2 + FS_BOOT_MIB))
P4=$((P3 + FS_BOOT_MIB))
P5=$((P4 + FS_EBR_GAP_MIB))
P6=$((P5 + FS_ROOT_MIB + FS_EBR_GAP_MIB))
END=$((P6 + FS_ROOT_MIB))
IMG="$OUT/freesounder-${VERSION}-ab.img"
rm -f "$IMG" "$IMG.xz"
truncate -s $((END * MIB)) "$IMG"
s() { echo $(($1 * MIB / 512)); }
sfdisk -q "$IMG" <<EOF
label: dos
label-id: $FS_DISK_ID
unit: sectors

start=$(s $P1), size=$(s $FS_BOOTSEL_MIB), type=e
start=$(s $P2), size=$(s $FS_BOOT_MIB), type=c
start=$(s $P3), size=$(s $FS_BOOT_MIB), type=c
start=$(s $P4), size=$(s $((END - P4))), type=5
start=$(s $P5), size=$(s $FS_ROOT_MIB), type=83
start=$(s $P6), size=$(s $FS_ROOT_MIB), type=83
EOF
# Non-zero MBR boot code (see pi-gen export-image): keeps old parted from rewriting the disk id.
printf '\xfa\xf4\xeb\xfd' | dd of="$IMG" conv=notrunc status=none
put() { dd if="$1" of="$IMG" bs=1M seek="$2" conv=notrunc,sparse status=none; }
put "$WORK/bootsel.vfat" $P1
put "$WORK/boot.vfat" $P2
put "$WORK/rootfs.ext4" $P5
# Root B is filled by the first update; stop the file right after root A (the partition table and
# p6's EBR are already written), so Imager writes ~4.7 GB instead of ~9 GB. p7 is made on first boot.
truncate -s $((P6 * MIB)) "$IMG"
sfdisk -d "$IMG" > "$OUT/layout.txt" 2>/dev/null || true
cp "$WORK/boot.vfat" "$OUT/boot.vfat"
cp --sparse=always "$WORK/rootfs.ext4" "$OUT/rootfs.ext4"
{
	echo "version=$VERSION"
	echo "disk_id=$FS_DISK_ID"
	echo "rootfs_bytes=$ROOTFS_BYTES"
	echo "image_bytes=$(stat -c %s "$IMG")"
	echo "full_layout_end_mib=$END"
} > "$OUT/ab-info.txt"

if [[ "${FS_NO_XZ:-0}" != 1 ]]; then
	echo "Compressing ..."
	xz -T0 -6 -f "$IMG"
	(cd "$OUT" && sha256sum "$(basename "$IMG").xz" > "$(basename "$IMG").xz.sha256")
fi
echo "A/B image ready in $OUT"
