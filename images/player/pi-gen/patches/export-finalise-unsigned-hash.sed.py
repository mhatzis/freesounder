"""After unmount, set unsigned_directory_hash and e2fsck -D like official Lite."""
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
if "rehash-rootfs-unsigned.sh not found" in text and 'REHASH="${REHASH_ROOTFS_UNSIGNED:-}"' in text:
    # Re-apply if a previous broken fallback line is present.
    pass
start = text.find("# Prefer fstrim (holes -> sparse bmap)")
if start < 0:
    raise SystemExit("fstrim needle missing")
u = text.find('unmount_image "${IMG_FILE}"', start)
if u < 0:
    raise SystemExit("unmount_image needle missing")
end = text.find("\n", u) + 1
new = """# Prefer fstrim (holes -> sparse bmap); fall back to zerofree if discard is unsupported.
ROOT_DEV="$(awk \\"\\$2 == \\"${ROOTFS_DIR}\\" {print \\$1}\\" /etc/mtab)"
if fstrim -v "${ROOTFS_DIR}"; then
	unmount "${ROOTFS_DIR}"
else
	unmount "${ROOTFS_DIR}"
	zerofree "${ROOT_DEV}"
fi

# Official Raspberry Pi OS Lite (ARM-built) uses unsigned_directory_hash.
# x86 mke2fs leaves signed_directory_hash. Flip the flag and rebuild
# directory indexes after the last host mount of this ext4 volume.
if [ -n "${ROOT_DEV}" ] && [ -b "${ROOT_DEV}" ]; then
	REHASH="${REHASH_ROOTFS_UNSIGNED:-}"
	if [ -z "${REHASH}" ]; then
		REHASH="$(cd "$(dirname "$0")" && pwd)/rehash-rootfs-unsigned.sh"
	fi
	if [ ! -x "${REHASH}" ]; then
		echo "rehash-rootfs-unsigned.sh not found; set REHASH_ROOTFS_UNSIGNED" >&2
		exit 1
	fi
	"${REHASH}" "${ROOT_DEV}"
fi

unmount_image "${IMG_FILE}"
"""
# The awk line above is over-escaped because I used a regular string.
# Write it with a raw-equivalent form:
new = (
    '# Prefer fstrim (holes -> sparse bmap); fall back to zerofree if discard is unsupported.\n'
    'ROOT_DEV="$(awk "\\$2 == \\"${ROOTFS_DIR}\\" {print \\$1}" /etc/mtab)"\n'
    'if fstrim -v "${ROOTFS_DIR}"; then\n'
    '\tunmount "${ROOTFS_DIR}"\n'
    'else\n'
    '\tunmount "${ROOTFS_DIR}"\n'
    '\tzerofree "${ROOT_DEV}"\n'
    'fi\n'
    '\n'
    '# Official Raspberry Pi OS Lite (ARM-built) uses unsigned_directory_hash.\n'
    '# x86 mke2fs leaves signed_directory_hash. Flip the flag and rebuild\n'
    '# directory indexes after the last host mount of this ext4 volume.\n'
    'if [ -n "${ROOT_DEV}" ] && [ -b "${ROOT_DEV}" ]; then\n'
    '\tREHASH="${REHASH_ROOTFS_UNSIGNED:-}"\n'
    '\tif [ -z "${REHASH}" ]; then\n'
    '\t\tREHASH="$(cd "$(dirname "$0")" && pwd)/rehash-rootfs-unsigned.sh"\n'
    '\tfi\n'
    '\tif [ ! -x "${REHASH}" ]; then\n'
    '\t\techo "rehash-rootfs-unsigned.sh not found; set REHASH_ROOTFS_UNSIGNED" >&2\n'
    '\t\texit 1\n'
    '\tfi\n'
    '\t"${REHASH}" "${ROOT_DEV}"\n'
    'fi\n'
    '\n'
    'unmount_image "${IMG_FILE}"\n'
)
p.write_text(text[:start] + new + text[end:])
print("finalise unsigned-hash patched")
