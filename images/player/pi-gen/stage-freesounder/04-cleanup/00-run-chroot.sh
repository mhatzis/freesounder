#!/bin/bash -e
# Drop apt's package lists and caches from the shipped image (~270 MB on
# disk, ~80 MB of the .img.xz). Nothing on a player runs apt; anyone who
# does from a shell runs `apt-get update` first anyway. The first image
# built on GitHub Actions (0.6.17-test1) still had them; 0.6.16 did not.
apt-get clean
rm -rf /var/lib/apt/lists/*
rm -f /var/cache/apt/pkgcache.bin /var/cache/apt/srcpkgcache.bin
