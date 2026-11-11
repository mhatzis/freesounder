#!/bin/bash -e
# Copy the fsadmin shadow hash from the build environment into the rootfs.
# The plaintext password must never be written into the tree or the log.
HASH="${FSADMIN_PASSWORD_HASH:-}"
if [[ -z "$HASH" && -n "${FSADMIN_PASSWORD_HASH_FILE:-}" && -f "${FSADMIN_PASSWORD_HASH_FILE}" ]]; then
	HASH="$(tr -d '\n' < "${FSADMIN_PASSWORD_HASH_FILE}")"
fi
if [[ -z "$HASH" ]]; then
	echo "FSADMIN_PASSWORD_HASH or FSADMIN_PASSWORD_HASH_FILE is required." >&2
	exit 1
fi
case "$HASH" in
	\$5\$*|\$6\$*|\$y\$*|\$gy\$*|\$2*) ;;
	*)
		echo "FSADMIN_PASSWORD_HASH is not a recognised crypt hash." >&2
		exit 1
		;;
esac
install -d -m 0755 "${ROOTFS_DIR}/etc/freesounder"
umask 077
printf '%s\n' "$HASH" > "${ROOTFS_DIR}/etc/freesounder/.fsadmin-hash"
chmod 0600 "${ROOTFS_DIR}/etc/freesounder/.fsadmin-hash"
# Avoid leaving the hash in this shell's environment dump.
unset HASH
