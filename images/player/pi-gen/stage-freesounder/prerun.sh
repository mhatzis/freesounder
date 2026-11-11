#!/bin/bash -e
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
# Build-host apt workarounds (removed again in 03-enable): SRV lookups for
# deb.debian.org hit a broken intercept, and some downloads need retries.
install -d "${ROOTFS_DIR}/etc/apt/apt.conf.d"
cat > "${ROOTFS_DIR}/etc/apt/apt.conf.d/80-freesounder-retries" <<'APT'
Acquire::ForceIPv4 "true";
Acquire::EnableSrvRecords "false";
Acquire::Retries "5";
Acquire::Queue-Mode "access";
Acquire::http::Pipeline-Depth "0";
APT
