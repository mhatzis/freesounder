#!/bin/bash
# After the last host mount of the rootfs, flip the ext4 directory-hash
# flag to unsigned (as official ARM-built Raspberry Pi OS Lite does) and
# rebuild directory indexes. x86 mke2fs sets signed_directory_hash, which
# does not match official Lite and is the confirmed on-image difference
# associated with EXT4 directory checksum errors on a Pi 5.
set -euo pipefail
ROOT_DEV=$1
if [ -z "${ROOT_DEV}" ] || [ ! -b "${ROOT_DEV}" ]; then
	echo "rehash-rootfs-unsigned: need a block device, got '${ROOT_DEV:-}'" >&2
	exit 1
fi
export PATH="/usr/local/sbin:/usr/sbin:/sbin:${PATH}"
if ! command -v debugfs >/dev/null || ! command -v e2fsck >/dev/null; then
	echo "rehash-rootfs-unsigned: debugfs and e2fsck are required" >&2
	exit 1
fi
echo "Setting unsigned_directory_hash on ${ROOT_DEV} and rehashing directories..."
debugfs -w -R 'ssv flags 2' "${ROOT_DEV}"
# e2fsck returns 1 when it has corrected the filesystem.
set +e
e2fsck -fy -D "${ROOT_DEV}"
rc=$?
set -e
if [ "$rc" -gt 1 ]; then
	echo "e2fsck -D failed with status ${rc}" >&2
	exit "$rc"
fi
e2fsck -fn "${ROOT_DEV}"
tune2fs -l "${ROOT_DEV}" | grep -E 'Filesystem flags|state'
