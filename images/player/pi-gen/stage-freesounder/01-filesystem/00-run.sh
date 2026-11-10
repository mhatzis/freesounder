#!/bin/bash -e
# Install freesounder filesystem overlay.
install -d "${ROOTFS_DIR}/etc/freesounder"
install -d "${ROOTFS_DIR}/usr/lib/freesounder"
install -d "${ROOTFS_DIR}/usr/sbin"
install -d "${ROOTFS_DIR}/etc/systemd/system"
install -d "${ROOTFS_DIR}/etc/update-motd.d"
install -d "${ROOTFS_DIR}/var/lib/freesounder/ma-data"
install -d "${ROOTFS_DIR}/opt/freesounder/control-api"
install -d "${ROOTFS_DIR}/opt/freesounder/player-agent"
install -d "${ROOTFS_DIR}/opt/freesounder/ble-provisioning"
install -d "${ROOTFS_DIR}/etc/default"
install -d "${ROOTFS_DIR}/etc/NetworkManager/system-connections"
install -d "${ROOTFS_DIR}/etc/alsa/conf.d"
install -d "${ROOTFS_DIR}/var/lib/freesounder"

install -m 0644 files/etc/freesounder/role "${ROOTFS_DIR}/etc/freesounder/role"
install -m 0644 files/etc/freesounder/host "${ROOTFS_DIR}/etc/freesounder/host"
install -m 0644 files/etc/freesounder/dac "${ROOTFS_DIR}/etc/freesounder/dac"
install -m 0755 files/usr/lib/freesounder/detect-role "${ROOTFS_DIR}/usr/lib/freesounder/detect-role"
install -m 0755 files/usr/lib/freesounder/apply-runtime-config "${ROOTFS_DIR}/usr/lib/freesounder/apply-runtime-config"
install -m 0755 files/usr/lib/freesounder/apply-dac-overlay "${ROOTFS_DIR}/usr/lib/freesounder/apply-dac-overlay"
install -m 0755 files/usr/lib/freesounder/apply-dsp-config "${ROOTFS_DIR}/usr/lib/freesounder/apply-dsp-config"
install -m 0755 files/usr/lib/freesounder/apply-player-agent "${ROOTFS_DIR}/usr/lib/freesounder/apply-player-agent"
install -m 0755 files/usr/lib/freesounder/run-music-assistant "${ROOTFS_DIR}/usr/lib/freesounder/run-music-assistant"
install -m 0755 files/usr/sbin/freesounder-setup "${ROOTFS_DIR}/usr/sbin/freesounder-setup"
install -m 0644 files/etc/systemd/system/freesounder-role.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-role.service"
install -m 0644 files/etc/systemd/system/freesounder-music-assistant.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-music-assistant.service"
install -m 0644 files/etc/systemd/system/freesounder-control-api.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-control-api.service"
install -m 0644 files/etc/systemd/system/freesounder-player-agent.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-player-agent.service"
install -m 0644 files/etc/systemd/system/freesounder-ble-provisioning.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-ble-provisioning.service"
install -m 0644 files/etc/default/snapclient "${ROOTFS_DIR}/etc/default/snapclient"
install -m 0644 files/etc/alsa/conf.d/99-freesounder.conf "${ROOTFS_DIR}/etc/alsa/conf.d/99-freesounder.conf"
install -m 0755 files/etc/update-motd.d/20-freesounder "${ROOTFS_DIR}/etc/update-motd.d/20-freesounder"

# Seed a DAC comment block so the overlay is discoverable before setup runs.
if [ -f "${ROOTFS_DIR}/boot/firmware/config.txt" ]; then
	if ! grep -q 'freesounder-dac-start' "${ROOTFS_DIR}/boot/firmware/config.txt"; then
		cat >> "${ROOTFS_DIR}/boot/firmware/config.txt" << 'CFG'

# --- freesounder-dac-start ---
# Official DAC+ and DigiAMP+ use the same overlay. Leave commented until
# you run: sudo freesounder-setup set-dac dacplus
# dtparam=audio=off
# dtoverlay=hifiberry-dacplus
# --- freesounder-dac-end ---
CFG
	fi
fi
