#!/bin/bash -e
# Install snapcast 0.35 (upstream trixie debs) and shairport-sync: the AirPlay 2
# rebuild + nqptp when staged, otherwise trixie's AirPlay 1 package.
export DEBIAN_FRONTEND=noninteractive
cd /tmp/fs-debs
apt-get update
apt-get install -y --no-install-recommends ./snapserver_*.deb ./snapclient_*.deb
airplay=$(cat airplay)
if [[ "$airplay" == 2 ]]; then
	apt-get install -y --no-install-recommends ./nqptp_*.deb ./shairport-sync_*.deb
else
	apt-get install -y --no-install-recommends shairport-sync
fi
install -d /etc/freesounder
printf '{"airplay": %s, "snapcast": "%s", "go_librespot": "%s", "shairport_sync": "%s"}\n' \
	"$airplay" "$(dpkg-query -W -f='${Version}' snapserver)" \
	"$(cat go-librespot-version)" \
	"$(dpkg-query -W -f='${Version}' shairport-sync)" > /etc/freesounder/features.json
cat /etc/freesounder/features.json
# Every audio binary must find its libraries (fail the build, not the first boot).
for bin in /usr/bin/snapserver /usr/bin/snapclient /usr/bin/shairport-sync /usr/local/bin/go-librespot /usr/bin/nqptp; do
	[[ -x "$bin" ]] || continue
	if ldd "$bin" | grep 'not found'; then
		echo "missing libraries for $bin" >&2
		exit 1
	fi
done
shairport-sync -V
cd /
rm -rf /tmp/fs-debs
