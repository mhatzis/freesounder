"""Insert an mtools flush of the boot staging dir before unmount in 05-finalise."""
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
old = '''# Prefer fstrim (holes -> sparse bmap); fall back to zerofree if discard is unsupported.
if fstrim -v "${ROOTFS_DIR}"; then
'''
new = '''# If the boot partition was staged on a bind mount (no vfat kernel), flush it now.
if [ -f "${STAGE_WORK_DIR}/boot.dev" ] && [ -d "${STAGE_WORK_DIR}/bootfs" ]; then
	echo "Flushing staged bootfs to FAT with mtools..."
	# Unmount the bind so we can empty the ext4 mountpoint afterwards.
	if findmnt -n "${ROOTFS_DIR}/boot/firmware" >/dev/null 2>&1; then
		umount "${ROOTFS_DIR}/boot/firmware"
	fi
	FLUSH_SCRIPT="${FLUSH_BOOTFS_MTOOLS:-}"
	if [ -z "${FLUSH_SCRIPT}" ]; then
		# Default: next to this patch script when applied from freesounder build.sh
		FLUSH_SCRIPT="$(cd "$(dirname "$0")" && pwd)/../flush-bootfs-mtools.sh"
	fi
	if [ ! -x "${FLUSH_SCRIPT}" ]; then
		echo "flush-bootfs-mtools.sh not found; set FLUSH_BOOTFS_MTOOLS" >&2
		exit 1
	fi
	"${FLUSH_SCRIPT}" "$(cat "${STAGE_WORK_DIR}/boot.dev")" "${STAGE_WORK_DIR}/bootfs"
	# Real Raspberry Pi OS keeps /boot/firmware as an empty mountpoint on ext4.
	rm -rf "${ROOTFS_DIR}/boot/firmware"/* "${ROOTFS_DIR}/boot/firmware"/.[!.]* 2>/dev/null || true
	mkdir -p "${ROOTFS_DIR}/boot/firmware"
fi

# Prefer fstrim (holes -> sparse bmap); fall back to zerofree if discard is unsupported.
if fstrim -v "${ROOTFS_DIR}"; then
'''
if 'Flushing staged bootfs to FAT with mtools' in text:
    print('05-finalise already patched')
elif old not in text:
    raise SystemExit('05-finalise needle missing')
else:
    p.write_text(text.replace(old, new, 1))
    print('05-finalise patched')
