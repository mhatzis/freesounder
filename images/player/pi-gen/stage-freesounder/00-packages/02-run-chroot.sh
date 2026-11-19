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
# UPnP/DLNA + OpenHome renderer (upmpdcli) on MPD. Debian's stock units stay
# off: freesounder-mpd.service runs MPD with our config, freesounderd starts
# freesounder-upnp.service on group leaders.
apt-get install -y --no-install-recommends ./libnpupnp13_*.deb ./libupnpp17_*.deb ./upmpdcli_*.deb
install -d /etc/freesounder
printf '{"airplay": %s, "snapcast": "%s", "go_librespot": "%s", "shairport_sync": "%s", "upnp": "%s", "mpd": "%s"}\n' \
	"$airplay" "$(dpkg-query -W -f='${Version}' snapserver)" \
	"$(cat go-librespot-version)" \
	"$(dpkg-query -W -f='${Version}' shairport-sync)" \
	"$(dpkg-query -W -f='${Version}' upmpdcli)" \
	"$(dpkg-query -W -f='${Version}' mpd)" > /etc/freesounder/features.json
cat /etc/freesounder/features.json
# Every audio binary must find its libraries (fail the build, not the first boot).
for bin in /usr/bin/snapserver /usr/bin/snapclient /usr/bin/shairport-sync /usr/local/bin/go-librespot /usr/bin/nqptp /usr/bin/mpd /usr/bin/upmpdcli; do
	[[ -x "$bin" ]] || continue
	if ldd "$bin" | grep 'not found'; then
		echo "missing libraries for $bin" >&2
		exit 1
	fi
done
shairport-sync -V
cd /
rm -rf /tmp/fs-debs
