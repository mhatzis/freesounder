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
shairport-sync -V || true
cd /
rm -rf /tmp/fs-debs
