"""If the kernel cannot mount vfat, bind-mount a staging dir over /boot/firmware."""
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
old = '''mount -v "$ROOT_DEV" "${ROOTFS_DIR}" -t ext4
mkdir -p "${ROOTFS_DIR}/boot/firmware"
mount -v "$BOOT_DEV" "${ROOTFS_DIR}/boot/firmware" -t vfat

rsync -rtx "${EXPORT_ROOTFS_DIR}/boot/firmware/" "${ROOTFS_DIR}/boot/firmware/"
'''
new = '''mount -v "$ROOT_DEV" "${ROOTFS_DIR}" -t ext4
mkdir -p "${ROOTFS_DIR}/boot/firmware"
if ! mount -v "$BOOT_DEV" "${ROOTFS_DIR}/boot/firmware" -t vfat; then
	echo "kernel has no vfat support; bind-mounting a staging dir (mtools flush at finalise)"
	mkdir -p "${STAGE_WORK_DIR}/bootfs"
	# Make sure the mountpoint is empty on ext4 before bind-mounting over it.
	mount --bind "${STAGE_WORK_DIR}/bootfs" "${ROOTFS_DIR}/boot/firmware"
	printf '%s\\n' "${BOOT_DEV}" > "${STAGE_WORK_DIR}/boot.dev"
fi

rsync -rtx "${EXPORT_ROOTFS_DIR}/boot/firmware/" "${ROOTFS_DIR}/boot/firmware/"
'''
if 'kernel has no vfat support' in text:
    print('prerun vfat fallback already patched')
elif old not in text:
    raise SystemExit('prerun vfat needle missing')
else:
    p.write_text(text.replace(old, new, 1))
    print('prerun vfat fallback patched')
