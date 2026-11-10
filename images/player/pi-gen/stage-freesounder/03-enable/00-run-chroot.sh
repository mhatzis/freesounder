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

# The first user is renamed on first boot but keeps supplementary groups.
if getent passwd pi >/dev/null; then
	for grp in docker audio gpio i2c spi video plugdev; do
		if getent group "$grp" >/dev/null; then
			usermod -aG "$grp" pi || true
		fi
	done
fi

# SSH is already requested via ENABLE_SSH=1; make sure the unit is on.
systemctl enable ssh.service || systemctl enable sshd.service || true

# Host-role first boot will docker pull; leave a stamp dir.
mkdir -p /var/lib/freesounder/ma-data
chmod 0755 /var/lib/freesounder /var/lib/freesounder/ma-data

# Drop the pi-gen build-host apt workaround so the shipped image uses normal apt.
rm -f /etc/apt/apt.conf.d/99-freesounder-build-apt
rm -f /etc/apt/apt.conf.d/99-freesounder-ipv4
