#!/bin/bash -e
# Installed by build.sh as pi-gen export-image/02-set-sources/02-run.sh, so
# it runs right after pi-gen's own `apt-get update` there (a clean-up in our
# stage was undone by it: 0.6.17/0.6.18 test images were ~80 MB bigger).
# Nothing on a player runs apt; a shell user runs `apt-get update` first.
find "${ROOTFS_DIR}/var/lib/apt/lists/" -type f -delete
rm -f "${ROOTFS_DIR}/var/cache/apt/pkgcache.bin" "${ROOTFS_DIR}/var/cache/apt/srcpkgcache.bin"
