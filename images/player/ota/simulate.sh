#!/bin/bash
# Hardware-free end-to-end check of the A/B update path, on loop devices instead of an SD card.
#
#   sudo simulate.sh <ab-image-dir> <plain-bundle.raucb> [work-dir]
#
# <ab-image-dir> is what make-ab-image.sh wrote (freesounder-*-ab.img, rootfs.ext4, boot.vfat).
# What it runs, with the real RAUC 1.13 service on a private D-Bus system bus and the real fsota.py,
# rauc-backend logic, post-install and data-setup scripts:
#   1. first boot: data-setup --grow-table creates p7 on the "card"; mkfs; state lives there
#   2. boot A; `freesounder-ota install` (RAUC writes p3+p6, post-install fixes them up); trial reboot
#   3. firmware model picks p3 from autoboot.txt + tryboot flag; boot-check; health passes -> commit
#   4. install again (now into A); trial boot A; health fails -> mark bad -> normal reboot -> back on B,
#      boot-check records the rollback
#   5. install again; trial boot; "hang" (power cut, no health) -> back on B, rolled back
#   6. manual rollback to the known-good other slot
# Differences from a player (needs hardware): the box has no dm-verity, so the bundle is "plain"; the
# Pi firmware is modelled by fsota.firmware_select; p1 is edited with mtools instead of a vfat mount.
# Nothing reboots: `systemctl` is stubbed inside the simulation and only records the request.
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"
[[ $EUID -eq 0 ]] || { echo "run as root (loop devices, mounts)" >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
LIB=$(cd "$HERE/../pi-gen/stage-freesounder/01-filesystem/files/usr/lib/freesounder/ota" && pwd)
SYSCONF=$HERE/../pi-gen/stage-freesounder/01-filesystem/files/etc/rauc/system.conf
DEV_KEYS=${OTA_DEV_KEYS:-${SUDO_HOME:-/home/${SUDO_USER:-box}}/.config/freesounder-build/ota-dev-keys}
KEYRING=${RAUC_KEYRING_PEM:-$DEV_KEYS/ca.cert.pem}
IMGDIR=$(realpath "${1:?ab-image-dir}")
BUNDLE=$(realpath "${2:?plain bundle}")
SIM=$(realpath -m "${3:-/var/tmp/fs-ota-sim}")
source "$HERE/layout.env"

PASS=0 FAIL=0
ok() { echo "  PASS  $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL  $*"; FAIL=$((FAIL + 1)); }
check() { local what=$1; shift; if "$@"; then ok "$what"; else bad "$what"; fi; }
step() { echo; echo "== $*"; }

DBUS_PID="" RAUC_PID=""
cleanup() {
	set +e
	[[ -n "$RAUC_PID" ]] && kill "$RAUC_PID" 2>/dev/null && wait "$RAUC_PID" 2>/dev/null
	[[ -n "$DBUS_PID" ]] && kill "$DBUS_PID" 2>/dev/null
	mountpoint -q "$SIM/data" && umount "$SIM/data"
	# part_loop runs in $(...) subshells, so find the loops by backing file.
	losetup -j "$SIM/card.img" -n -O NAME 2>/dev/null | xargs -r -n1 losetup -d
}
trap cleanup EXIT

rm -rf "$SIM"
mkdir -p "$SIM"/{bin,data,dt,running/etc/ssh,running/boot/firmware,mnt}
IMG=$(ls "$IMGDIR"/*-ab.img | head -1)

step "card: A/B image written to a 10 GiB 'SD card'"
cp --sparse=always "$IMG" "$SIM/card.img"
truncate -s 10G "$SIM/card.img"
sfdisk -d "$SIM/card.img" > "$SIM/table.before"
check "image has the fixed disk id $FS_DISK_ID" rg -q "label-id: $FS_DISK_ID" "$SIM/table.before"
check "image ships 6 partitions (p7 comes at first boot)" test "$(rg -c '^/' "$SIM/table.before")" -eq 6

step "first boot: data-setup --grow-table"
"$LIB/data-setup" --grow-table "$SIM/card.img"
sfdisk -d "$SIM/card.img" > "$SIM/table.after"
check "p7 (data) created" rg -q 'card.img7 :' "$SIM/table.after"
check "p1-p6 unchanged" diff <(rg '^/.*img[1-35-6] :' "$SIM/table.before") <(rg '^/.*img[1-35-6] :' "$SIM/table.after")
p7_end=$(sfdisk -J "$SIM/card.img" | python3 -c 'import json,sys; p=json.load(sys.stdin)["partitiontable"]["partitions"][-1]; print((p["start"]+p["size"])*512)')
check "p7 reaches the end of the card" test $(( $(stat -c %s "$SIM/card.img") - p7_end )) -lt $((1024 * 1024))

part_loop() {
	local n=$1
	read -r start size < <(sfdisk -J "$SIM/card.img" | python3 -c "
import json,sys
for p in json.load(sys.stdin)['partitiontable']['partitions']:
    if p['node'].endswith('img$n'): print(p['start']*512, p['size']*512)")
	local l
	l=$(losetup --find --show --offset "$start" --sizelimit "$size" "$SIM/card.img")
	echo "$l"
}
P1=$(part_loop 1); P2=$(part_loop 2); P3=$(part_loop 3); P5=$(part_loop 5); P6=$(part_loop 6); P7=$(part_loop 7)
echo "  p1=$P1 p2=$P2 p3=$P3 p5=$P5 p6=$P6 p7=$P7"
mkfs.ext4 -q -L fsdata "$P7"
mount "$P7" "$SIM/data"
mkdir -p "$SIM/data/ota"  # data-setup does this on a player
export MTOOLS_SKIP_CHECK=1
# mtools warns "Could not get geometry" on loop devices; harmless.
mtype() { command mtype "$@" 2>/dev/null; }
export -f mtype
check "p1 autoboot.txt defaults to slot A" bash -c "mtype -i $P1 ::/autoboot.txt | rg -q '^boot_partition=2' "

# The player's running system (identity post-install must carry over).
echo "sim-machine-id-0123456789abcdef" > "$SIM/running/etc/machine-id"
echo "fake host key for the simulation" > "$SIM/running/etc/ssh/ssh_host_ed25519_key"
chmod 600 "$SIM/running/etc/ssh/ssh_host_ed25519_key"
mtype -i "$P2" ::/config.txt > "$SIM/running/boot/firmware/config.txt"
printf '# --- freesounder-dac-start ---\ndtoverlay=hifiberry-dacplus-std\n# --- freesounder-dac-end ---\n' \
	>> "$SIM/running/boot/firmware/config.txt"

# Stubs and wrappers. systemctl only records reboot requests (the firmware model acts on them).
cat > "$SIM/bin/systemctl" <<EOF
#!/bin/sh
echo "\$*" >> "$SIM/reboot-requests"
case "\$*" in *tryboot*) echo 1 > "$SIM/tryboot-flag" ;; *reboot*) echo 0 > "$SIM/tryboot-flag" ;; esac
EOF
cat > "$SIM/env" <<EOF
export PATH="$SIM/bin:\$PATH"
export FSOTA_STATE_DIR="$SIM/data/ota"
export FSOTA_CMDLINE="$SIM/cmdline"
export FSOTA_DT_BOOTLOADER="$SIM/dt"
export FSOTA_BOOTSEL_DEV="$P1"
export FSOTA_BOOTSEL_MTOOLS=1
export MTOOLS_SKIP_CHECK=1
export DBUS_SYSTEM_BUS_ADDRESS="unix:path=$SIM/dbus.sock"
EOF
cat > "$SIM/bin/rauc-backend" <<EOF
#!/bin/sh
. "$SIM/env"
echo "rauc-backend \$*" >> "$SIM/backend.log"
exec python3 -c 'import sys; sys.path.insert(0, "$LIB"); import fsota; sys.exit(fsota.backend(sys.argv[1:]))' "\$@"
EOF
cat > "$SIM/bin/freesounder-ota" <<EOF
#!/bin/sh
. "$SIM/env"
exec python3 -c 'import sys; sys.path.insert(0, "$LIB"); import fsota; sys.exit(fsota.main(sys.argv[1:]))' "\$@"
EOF
cat > "$SIM/bin/post-install" <<EOF
#!/bin/sh
env | grep '^RAUC_' | sort > "$SIM/post-install.env"
FSPI_ROOT="$SIM/running" exec "$LIB/post-install"
EOF
chmod +x "$SIM"/bin/*

# The real system.conf with the devices pointed at the loops, plain bundles allowed, files in $SIM.
python3 - "$SYSCONF" "$SIM/system.conf" <<EOF
import re, sys
s = open(sys.argv[1]).read()
for part, dev in {"05": "$P5", "06": "$P6", "02": "$P2", "03": "$P3"}.items():
    s = s.replace("/dev/disk/by-partuuid/46534f54-" + part, dev)
s = s.replace("bundle-formats=-plain", "bundle-formats=plain verity")
s = s.replace("statusfile=/data/ota/rauc.status", "statusfile=$SIM/data/ota/rauc.status")
s = s.replace("mountprefix=/run/rauc", "mountprefix=$SIM/mnt")
s = s.replace("path=/etc/rauc/keyring.pem", "path=$KEYRING")
s = s.replace("/usr/lib/freesounder/ota/rauc-backend", "$SIM/bin/rauc-backend")
s = s.replace("/usr/lib/freesounder/ota/post-install", "$SIM/bin/post-install")
open(sys.argv[2], "w").write(s)
EOF

cat > "$SIM/dbus.conf" <<EOF
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>system</type>
  <listen>unix:path=$SIM/dbus.sock</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow user="*"/><allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/>
  </policy>
</busconfig>
EOF
DBUS_PID=$(dbus-daemon --config-file="$SIM/dbus.conf" --fork --print-pid)
. "$SIM/env"

# ---------------------------------------------------------------------------- the firmware model
power_on() {
	# Pi firmware: read autoboot.txt from p1, honour (and clear) the one-shot tryboot flag.
	local flag=0 part slot root
	[[ -f "$SIM/tryboot-flag" ]] && flag=$(cat "$SIM/tryboot-flag")
	rm -f "$SIM/tryboot-flag"
	part=$(mtype -i "$P1" ::/autoboot.txt | python3 -c "
import sys; sys.path.insert(0, '$LIB'); import fsota
print(fsota.firmware_select(sys.stdin.read(), tryboot=$flag == 1))")
	case "$part" in 2) slot=A root=05 ;; 3) slot=B root=06 ;; *) echo "firmware: no bootable slot ($part)"; return 1 ;; esac
	# The cmdline the firmware hands over comes from the chosen boot partition's [boot_partition=N] filter.
	local cmdfile
	cmdfile=$(mtype -i "$( [[ $slot == A ]] && echo "$P2" || echo "$P3")" ::/config.txt \
		| python3 -c "
import sys
sec, cmd = 'all', 'cmdline.txt'
for l in sys.stdin.read().splitlines():
    l = l.strip()
    if l.startswith('['): sec = l.strip('[]'); continue
    if l.startswith('cmdline=') and sec in ('all', 'boot_partition=$part'): cmd = l.split('=', 1)[1]
print(cmd)")
	mtype -i "$( [[ $slot == A ]] && echo "$P2" || echo "$P3")" "::/$cmdfile" > "$SIM/cmdline"
	python3 -c "import sys; open('$SIM/dt/tryboot','wb').write(int($flag).to_bytes(4,'big')); open('$SIM/dt/partition','wb').write(int($part).to_bytes(4,'big'))"
	echo "  firmware: tryboot=$flag -> p$part ($cmdfile) -> slot $slot: $(rg -o 'root=\S+|rauc.slot=\S+' "$SIM/cmdline" | tr '\n' ' ')"
	# Early boot: freesounder-ota-boot.service, then rauc.service.
	echo "  boot-check: $(freesounder-ota boot-check)"
	[[ -n "$RAUC_PID" ]] && { kill "$RAUC_PID"; wait "$RAUC_PID" 2>/dev/null || true; }
	# The box kernel cmdline has no rauc.slot=; a player's does (cmdline_a/b.txt), so tell RAUC here.
	rauc --conf="$SIM/system.conf" --override-boot-slot="$slot" service >> "$SIM/rauc-service.log" 2>&1 &
	RAUC_PID=$!
	for _ in $(seq 50); do rauc status >/dev/null 2>&1 && break; sleep 0.2; done
	BOOTED=$slot
}
st() { python3 -c "import json,sys; d=json.load(open('$SIM/data/ota/state.json')); print(eval(sys.argv[1]))" "$1"; }
default_part() { mtype -i "$P1" ::/autoboot.txt | python3 -c "
import sys; sys.path.insert(0, '$LIB'); import fsota; print(fsota.firmware_select(sys.stdin.read(), False))"; }
rauc_booted() { rauc status --output-format=json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["booted"])'; }
install() {
	rm -f "$SIM/post-install.env"
	if freesounder-ota install "$BUNDLE" > "$SIM/install.log" 2>&1; then return 0; fi
	tail -20 "$SIM/install.log"; return 1
}

# Slot A initially holds the factory image; B is empty.
step "boot 1: factory image on A"
power_on
check "booted A" test "$BOOTED" = A
check "RAUC service reports booted slot A" test "$(rauc_booted)" = A
rauc status > "$SIM/status-0.txt" 2>&1 || true

step "bundles a player must refuse"
mkdir -p "$SIM/tiny" "$SIM/evil-keys"
truncate -s 4M "$SIM/tiny/rootfs.ext4" "$SIM/tiny/boot.vfat"
mkfs.ext4 -q "$SIM/tiny/rootfs.ext4"; mkfs.vfat "$SIM/tiny/boot.vfat" >/dev/null
# A different CA + signer (throwaway, never printed).
(
	cd "$SIM/evil-keys"
	openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout ca.key -out ca.cert -days 2 \
		-subj /CN=evil-ca -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign 2>/dev/null
	openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout s.key -out s.csr -subj /CN=evil 2>/dev/null
	printf 'keyUsage=critical,digitalSignature\nextendedKeyUsage=critical,codeSigning\n' > ext
	openssl x509 -req -in s.csr -CA ca.cert -CAkey ca.key -CAcreateserial -out s.cert -days 2 -extfile ext 2>/dev/null
)
"$HERE/build-bundle.sh" --images "$SIM/tiny" --version evil --out "$SIM/evil.raucb" --format plain \
	--cert "$SIM/evil-keys/s.cert" --key "$SIM/evil-keys/s.key" --keyring "$SIM/evil-keys/ca.cert" >/dev/null
"$HERE/build-bundle.sh" --images "$SIM/tiny" --version other --out "$SIM/other.raucb" --format plain \
	--compatible some-other-device >/dev/null
check "bundle signed by an unknown CA is refused" bash -c "! rauc install $SIM/evil.raucb >$SIM/evil.log 2>&1"
check "  (reason: signature)" rg -qi "signature|verif|certificate" "$SIM/evil.log"
check "bundle for another device is refused" bash -c "! rauc install $SIM/other.raucb >$SIM/other.log 2>&1"
check "  (reason: compatible)" rg -qi compatible "$SIM/other.log"
check "nothing pending after refused bundles" test "$(python3 -c "import json,os; p='$SIM/data/ota/state.json'; print(json.load(open(p)).get('pending') if os.path.exists(p) else None)")" = None
check "verity bundles are verified too (rauc info)" bash -c "rauc --conf=$SIM/system.conf info ${VERITY_BUNDLE:-$BUNDLE} >/dev/null 2>&1"

step "update 1: install into B, trial boot, health passes -> commit"
check "freesounder-ota install (rauc install over D-Bus)" install
check "RAUC wrote root B (fsck clean)" e2fsck -fn "$P6" >/dev/null 2>&1
check "post-install grew root B to the partition" test "$(dumpe2fs -h "$P6" 2>/dev/null | awk -F: '/^Block count/{print $2*4096}')" -eq "$(blockdev --getsize64 "$P6")"
mkdir -p "$SIM/inspect" && mount -o ro "$P6" "$SIM/inspect"
check "machine-id carried into B" cmp -s "$SIM/running/etc/machine-id" "$SIM/inspect/etc/machine-id"
check "SSH host key carried into B" cmp -s "$SIM/running/etc/ssh/ssh_host_ed25519_key" "$SIM/inspect/etc/ssh/ssh_host_ed25519_key"
umount "$SIM/inspect"
check "boot B config.txt has the DAC block" bash -c "mtype -i $P3 ::/config.txt | rg -q hifiberry-dacplus-std"
check "boot B has cmdline_a/b" bash -c "mtype -i $P3 ::/cmdline_b.txt | rg -q 'rauc.slot=B'"
check "post-install saw RAUC_TARGET_SLOTS + slot classes" bash -c "rg -q RAUC_SLOT_CLASS_ $SIM/post-install.env"
check "pending = B" test "$(st 'd["pending"]')" = B
check "autoboot default still A (install never changes the normal boot)" test "$(default_part)" = 2
check "a tryboot reboot was requested" rg -q tryboot "$SIM/reboot-requests"
power_on
check "trial boot runs B" test "$BOOTED" = B
check "boot-check opened a trial" test "$(st 'd["trial"]["slot"]')" = B
check "health passes and commits (rauc status mark-good -> backend)" env FSOTA_FAKE_HEALTH=pass freesounder-ota health --timeout 0
check "autoboot default is now B" test "$(default_part)" = 3
check "A and B both good" test "$(st 'd["slots"]')" = "{'A': 'good', 'B': 'good'}"
power_on
check "next normal power-on stays on B" test "$BOOTED" = B

step "update 2: install into A, trial boot, health fails -> automatic rollback"
check "install into A" install
check "pending = A" test "$(st 'd["pending"]')" = A
power_on
check "trial boot runs A" test "$BOOTED" = A
! env FSOTA_FAKE_HEALTH=fail freesounder-ota health --timeout 0 > "$SIM/health-fail.log" 2>&1
check "health failure requested a normal reboot" test "$(tail -1 "$SIM/reboot-requests")" = reboot
power_on
check "firmware falls back to B" test "$BOOTED" = B
check "A marked bad" test "$(st 'd["slots"]["A"]')" = bad
check "last_result = rolled_back with the reason" bash -c "test \"\$(python3 -c \"import json; d=json.load(open('$SIM/data/ota/state.json'))['last_result']; print(d['result'], 'simulated failure' in d['reason'])\")\" = 'rolled_back True'"
check "autoboot default still B" test "$(default_part)" = 3

step "update 3: trial boot hangs / loses power before the health check"
check "install into A" install
power_on
check "trial boot runs A" test "$BOOTED" = A
power_on   # watchdog reset / power cut: tryboot flag is gone
check "back on B" test "$BOOTED" = B
check "rolled back" test "$(st 'd["last_result"]["result"]')" = rolled_back

step "manual rollback (freesounder-ota rollback) to the known-good other slot"
check "update 4: install into A" install
power_on
FSOTA_FAKE_HEALTH=pass freesounder-ota health --timeout 0 >/dev/null
check "A committed" test "$(default_part)" = 2
check "rollback accepted" freesounder-ota rollback
power_on
check "trial boot of B" test "$BOOTED" = B
FSOTA_FAKE_HEALTH=pass freesounder-ota health --timeout 0 >/dev/null
check "B is the default again" test "$(default_part)" = 3

echo
echo "simulation: $PASS passed, $FAIL failed (logs in $SIM)"
((FAIL == 0))
