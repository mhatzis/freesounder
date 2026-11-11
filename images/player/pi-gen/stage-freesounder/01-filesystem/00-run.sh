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

# Bake the default hostname even when stages 0–2 were built with an older name.
printf 'fs-new\n' > "${ROOTFS_DIR}/etc/hostname"
if [[ -f "${ROOTFS_DIR}/etc/hosts" ]]; then
	sed -i 's/[[:space:]]freesounder$/	fs-new/' "${ROOTFS_DIR}/etc/hosts" || true
fi
install -m 0644 files/etc/freesounder/role "${ROOTFS_DIR}/etc/freesounder/role"
install -m 0644 files/etc/freesounder/host "${ROOTFS_DIR}/etc/freesounder/host"
install -m 0644 files/etc/freesounder/dac "${ROOTFS_DIR}/etc/freesounder/dac"
install -m 0755 files/usr/lib/freesounder/detect-role "${ROOTFS_DIR}/usr/lib/freesounder/detect-role"
install -m 0755 files/usr/lib/freesounder/apply-runtime-config "${ROOTFS_DIR}/usr/lib/freesounder/apply-runtime-config"
install -m 0755 files/usr/lib/freesounder/apply-dac-overlay "${ROOTFS_DIR}/usr/lib/freesounder/apply-dac-overlay"
install -m 0755 files/usr/lib/freesounder/apply-dsp-config "${ROOTFS_DIR}/usr/lib/freesounder/apply-dsp-config"
install -m 0755 files/usr/lib/freesounder/apply-player-agent "${ROOTFS_DIR}/usr/lib/freesounder/apply-player-agent"
install -m 0755 files/usr/lib/freesounder/run-music-assistant "${ROOTFS_DIR}/usr/lib/freesounder/run-music-assistant"
install -m 0755 files/usr/lib/freesounder/run-boot-doctor "${ROOTFS_DIR}/usr/lib/freesounder/run-boot-doctor"
install -m 0755 files/usr/lib/freesounder/write-boot-marker "${ROOTFS_DIR}/usr/lib/freesounder/write-boot-marker"
install -m 0755 files/usr/lib/freesounder/set-hostname "${ROOTFS_DIR}/usr/lib/freesounder/set-hostname"
install -m 0755 files/usr/lib/freesounder/apply-hostname "${ROOTFS_DIR}/usr/lib/freesounder/apply-hostname"
install -m 0755 files/usr/lib/freesounder/status-console "${ROOTFS_DIR}/usr/lib/freesounder/status-console"
install -m 0755 files/usr/lib/freesounder/sanitize-imager "${ROOTFS_DIR}/usr/lib/freesounder/sanitize-imager"
install -m 0755 files/usr/lib/freesounder/ssh-developer "${ROOTFS_DIR}/usr/lib/freesounder/ssh-developer"
install -m 0755 files/usr/sbin/freesounder-setup "${ROOTFS_DIR}/usr/sbin/freesounder-setup"
install -m 0644 files/etc/systemd/system/freesounder-role.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-role.service"
install -m 0644 files/etc/systemd/system/freesounder-music-assistant.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-music-assistant.service"
install -m 0644 files/etc/systemd/system/freesounder-control-api.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-control-api.service"
install -m 0644 files/etc/systemd/system/freesounder-player-agent.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-player-agent.service"
install -m 0644 files/etc/systemd/system/freesounder-ble-provisioning.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-ble-provisioning.service"
install -d "${ROOTFS_DIR}/etc/systemd/system/bluetooth.service.d"
install -m 0644 files/etc/systemd/system/bluetooth.service.d/10-freesounder-experimental.conf \
	"${ROOTFS_DIR}/etc/systemd/system/bluetooth.service.d/10-freesounder-experimental.conf"
install -m 0644 files/etc/systemd/system/freesounder-boot-doctor.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-boot-doctor.service"
install -m 0644 files/etc/systemd/system/freesounder-boot-doctor.timer "${ROOTFS_DIR}/etc/systemd/system/freesounder-boot-doctor.timer"
install -m 0644 files/etc/systemd/system/freesounder-boot-mark.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-boot-mark.service"
install -m 0644 files/etc/systemd/system/freesounder-boot-sysinit.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-boot-sysinit.service"
install -m 0644 files/etc/systemd/system/freesounder-boot-doctor-early.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-boot-doctor-early.service"
install -m 0644 files/etc/systemd/system/freesounder-hostname.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-hostname.service"
install -m 0644 files/etc/systemd/system/freesounder-status.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-status.service"
install -m 0644 files/etc/systemd/system/freesounder-sanitize-imager.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-sanitize-imager.service"
install -m 0644 files/etc/systemd/system/freesounder-ssh-reset.service "${ROOTFS_DIR}/etc/systemd/system/freesounder-ssh-reset.service"
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

install -d "${ROOTFS_DIR}/etc/cloud/cloud.cfg.d"
install -m 0644 files/etc/cloud/cloud.cfg.d/99-freesounder-appliance.cfg \
	"${ROOTFS_DIR}/etc/cloud/cloud.cfg.d/99-freesounder-appliance.cfg"
install -d "${ROOTFS_DIR}/etc/systemd/system/cloud-init-network.service.d"
install -d "${ROOTFS_DIR}/etc/systemd/system/cloud-init-local.service.d"
install -d "${ROOTFS_DIR}/etc/systemd/system/cloud-init-main.service.d"
install -m 0644 files/etc/systemd/system/cloud-init-network.service.d/10-freesounder-timeout.conf \
	"${ROOTFS_DIR}/etc/systemd/system/cloud-init-network.service.d/10-freesounder-timeout.conf"
install -m 0644 files/etc/systemd/system/cloud-init-local.service.d/10-freesounder-timeout.conf \
	"${ROOTFS_DIR}/etc/systemd/system/cloud-init-local.service.d/10-freesounder-timeout.conf"
install -m 0644 files/etc/systemd/system/cloud-init-main.service.d/10-freesounder-timeout.conf \
	"${ROOTFS_DIR}/etc/systemd/system/cloud-init-main.service.d/10-freesounder-timeout.conf"
install -d "${ROOTFS_DIR}/etc/initramfs-tools/scripts/local-bottom"
install -m 0755 files/etc/initramfs-tools/scripts/local-bottom/00-freesounder-mark \
	"${ROOTFS_DIR}/etc/initramfs-tools/scripts/local-bottom/00-freesounder-mark"

# Keep HDMI kernel messages visible (no quiet). loglevel=7 helps with a
# micro-HDMI cable if the board hangs during boot.
if [ -f "${ROOTFS_DIR}/boot/firmware/cmdline.txt" ]; then
	sed -i 's/[[:space:]]quiet//g' "${ROOTFS_DIR}/boot/firmware/cmdline.txt"
	if ! grep -q 'loglevel=' "${ROOTFS_DIR}/boot/firmware/cmdline.txt"; then
		# cmdline is a single line
		sed -i 's/[[:space:]]*$/ loglevel=7/' "${ROOTFS_DIR}/boot/firmware/cmdline.txt"
	fi
fi
