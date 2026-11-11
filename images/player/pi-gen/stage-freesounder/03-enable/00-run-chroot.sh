#!/bin/bash -e
# Enable freesounder units. Every player is equal: freesounderd decides at
# runtime whether this player leads a group (snapserver, Spotify Connect,
# AirPlay via snapserver, radio) or follows another one (snapclient only).
systemctl enable avahi-daemon.service
systemctl enable freesounderd.service
systemctl enable snapclient.service || true
# Started/stopped by freesounderd, not at boot.
systemctl disable snapserver.service 2>/dev/null || true
# snapserver launches shairport-sync itself (one AirPlay receiver per group).
systemctl disable shairport-sync.service 2>/dev/null || true
systemctl mask shairport-sync.service 2>/dev/null || true
# AirPlay 2 timing helper (only present with the AirPlay 2 build).
systemctl enable nqptp.service 2>/dev/null || true
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
	for grp in audio gpio i2c spi video plugdev; do
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

# fsadmin is the local console account. The shadow hash is supplied at
# image-build time (never stored in the repo) and is deleted after apply.
hash_file=/etc/freesounder/.fsadmin-hash
if [[ ! -s "$hash_file" ]]; then
	echo "fsadmin password hash is missing; pass FSADMIN_PASSWORD_HASH when building." >&2
	exit 1
fi
if ! getent passwd fsadmin >/dev/null; then
	useradd -m -s /bin/bash -G sudo fsadmin
fi
for grp in audio gpio i2c spi video plugdev sudo; do
	if getent group "$grp" >/dev/null; then
		usermod -aG "$grp" fsadmin || true
	fi
done
# chpasswd -e reads user:hash. Do not print the hash.
{
	printf 'fsadmin:'
	tr -d '\n' < "$hash_file"
	printf '\n'
} | chpasswd -e
if command -v shred >/dev/null; then
	shred -u "$hash_file" 2>/dev/null || rm -f "$hash_file"
else
	rm -f "$hash_file"
fi

mkdir -p /var/lib/freesounder/spotify
chmod 0755 /var/lib/freesounder
chmod 0700 /var/lib/freesounder/spotify

# Drop the pi-gen build-host apt workaround so the shipped image uses normal apt.
rm -f /etc/apt/apt.conf.d/99-freesounder-build-apt /etc/apt/apt.conf.d/80-freesounder-retries
rm -f /etc/apt/apt.conf.d/99-freesounder-ipv4
