#!/bin/bash -e
# Enable freesounder units and grant the first user Docker/audio access.
systemctl enable docker.service
systemctl enable avahi-daemon.service
systemctl enable freesounder-role.service
systemctl enable freesounder-music-assistant.service
systemctl enable freesounder-control-api.service
systemctl enable freesounder-player-agent.service
# snapclient ships its own unit; keep it enabled.
systemctl enable snapclient.service || true
# Onboard Bluetooth for Improv Wi-Fi setup. No A2DP / Bluetooth audio is registered.
systemctl enable bluetooth.service || true
systemctl enable NetworkManager.service || true
systemctl enable freesounder-ble-provisioning.service
systemctl enable freesounder-boot-doctor.timer
systemctl enable freesounder-boot-mark.service
systemctl enable freesounder-boot-sysinit.service
systemctl enable freesounder-boot-doctor-early.service

# The first user is renamed on first boot but keeps supplementary groups.
if getent passwd pi >/dev/null; then
	for grp in docker audio gpio i2c spi video plugdev; do
		if getent group "$grp" >/dev/null; then
			usermod -aG "$grp" pi || true
		fi
	done
fi

# Locked-down appliance: no first-user wizard, no SSH by default, tty1 is
# a status screen. Developer SSH is enabled only from the app and resets
# on reboot.
systemctl disable --now ssh.service ssh.socket 2>/dev/null || true
systemctl disable sshswitch.service 2>/dev/null || true
systemctl mask sshswitch.service userconfig.service 2>/dev/null || true
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl mask getty@tty1.service 2>/dev/null || true
systemctl enable freesounder-hostname.service
systemctl enable freesounder-status.service
systemctl enable freesounder-sanitize-imager.service
systemctl enable freesounder-ssh-reset.service

# Keep the placeholder pi account locked and unusable for login.
if getent passwd pi >/dev/null; then
	passwd -l pi || true
	usermod -s /usr/sbin/nologin pi || true
fi
passwd -l root || true
if ! getent passwd developer >/dev/null; then
	useradd -m -s /usr/sbin/nologin -G sudo developer || useradd -m -s /usr/sbin/nologin developer
fi
passwd -l developer || true

# Host-role first boot will docker pull; leave a stamp dir.
mkdir -p /var/lib/freesounder/ma-data
chmod 0755 /var/lib/freesounder /var/lib/freesounder/ma-data

# Drop the pi-gen build-host apt workaround so the shipped image uses normal apt.
rm -f /etc/apt/apt.conf.d/99-freesounder-build-apt
rm -f /etc/apt/apt.conf.d/99-freesounder-ipv4
