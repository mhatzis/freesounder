#!/bin/bash
# Flush a staging directory onto a raw FAT partition using mtools.
# Used when the build host kernel has no vfat support.
# Usage: flush-bootfs-mtools.sh <boot-device> <staging-dir>
set -euo pipefail
BOOT_DEV=$1
BOOTFS=$2
export MTOOLS_SKIP_CHECK=1
export MTOOLS_NO_VFAT=0
if ! command -v mcopy >/dev/null 2>&1; then
	echo "mcopy (mtools) is required to write the FAT boot partition without a vfat kernel" >&2
	exit 1
fi
# Create directories then copy files. -o overwrites if the script is re-run.
# LC_ALL=C so find order is stable; sort directories so parents come first.
while IFS= read -r d; do
	[ -z "$d" ] && continue
	mmd -i "${BOOT_DEV}" "::${d}" 2>/dev/null || true
done < <(cd "${BOOTFS}" && find . -type d ! -path . -printf '%P\n' | sort)

while IFS= read -r f; do
	[ -z "$f" ] && continue
	mcopy -o -i "${BOOT_DEV}" "${BOOTFS}/${f}" "::${f}"
done < <(cd "${BOOTFS}" && find . -type f -printf '%P\n')
echo "Flushed $(find "${BOOTFS}" -type f | wc -l) boot files to ${BOOT_DEV}"
