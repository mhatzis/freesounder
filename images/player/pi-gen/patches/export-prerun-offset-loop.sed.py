"""Patch pi-gen export-image/prerun.sh to use offset losetup when loop.max_part=0."""
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
old = '''ensure_loopdev_partitions "$LOOP_DEV"
BOOT_DEV="${LOOP_DEV}p1"
ROOT_DEV="${LOOP_DEV}p2"
'''
new = '''ensure_loopdev_partitions "$LOOP_DEV"
if [ -b "${LOOP_DEV}p1" ] && [ -b "${LOOP_DEV}p2" ]; then
	BOOT_DEV="${LOOP_DEV}p1"
	ROOT_DEV="${LOOP_DEV}p2"
else
	# This kernel's loop driver has max_part=0 (and device-mapper/kpartx
	# may be unavailable), so partition nodes like loop0p1 are never created.
	# Attach each partition as its own loop device by byte offset.
	echo "loop partition nodes missing; using offset losetup"
	losetup -d "$LOOP_DEV" || true
	BOOT_DEV="$(losetup --show --find --offset "${BOOT_PART_START}" --sizelimit "${BOOT_PART_SIZE}" "$IMG_FILE")"
	ROOT_DEV="$(losetup --show --find --offset "${ROOT_PART_START}" --sizelimit "${ROOT_PART_SIZE}" "$IMG_FILE")"
	# Keep names visible to later unmount_image (losetup -j IMG_FILE).
	echo "BOOT_DEV=${BOOT_DEV} ROOT_DEV=${ROOT_DEV}"
fi
'''
if 'using offset losetup' in text:
    print('prerun.sh already patched')
elif old not in text:
    raise SystemExit('prerun.sh needle missing')
else:
    p.write_text(text.replace(old, new, 1))
    print('prerun.sh patched')
