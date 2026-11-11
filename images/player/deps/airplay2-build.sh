#!/bin/bash
# Build AirPlay 2 capable shairport-sync and nqptp .debs for trixie arm64 from
# Debian sid sources. Run inside a debian:trixie arm64 container.
set -euxo pipefail
OUT=$(realpath -m "${1:-out}")
SHAIRPORT_VERSION=${SHAIRPORT_VERSION:-5.6.2-3}
NQPTP_VERSION=${NQPTP_VERSION:-1.2.9-1}
export DEBIAN_FRONTEND=noninteractive
cat > /etc/apt/sources.list.d/sid-src.sources <<SRC
Types: deb-src
URIs: http://deb.debian.org/debian
Suites: sid
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SRC
apt-get update
apt-get install -y --no-install-recommends build-essential devscripts dpkg-dev fakeroot equivs ca-certificates
mkdir -p "$OUT" /tmp/src
cd /tmp/src
for spec in "nqptp=${NQPTP_VERSION}" "shairport-sync=${SHAIRPORT_VERSION}"; do
	name=${spec%%=*}
	apt-get source --download-only "$spec"
	dpkg-source -x ./${name}_*.dsc "$name-src"
	(
		cd "$name-src"
		# sid uses debhelper compat 14; trixie's debhelper 13.24 builds it at 13
		# (single binary package, so dh_auto_install already targets it).
		sed -i -E 's/debhelper-compat \(= 14\)/debhelper-compat (= 13)/; s/,?[[:space:]]*dh-sequence-single-binary//' debian/control
		grep -n "Build-Depends" debian/control
		# Satisfy Build-Depends from trixie.
		mk-build-deps -i -r -t "apt-get -y --no-install-recommends" debian/control
		dch --local "~fs13" --distribution trixie "Rebuild for Raspberry Pi OS trixie (FreeSounder)."
		dpkg-buildpackage -b -uc -us -j"$(nproc)"
	)
done
cp /tmp/src/*.deb "$OUT/"
rm -f "$OUT"/*-dbgsym_*.deb "$OUT"/*-build-deps_*.deb
ls -l "$OUT"
for deb in "$OUT"/*.deb; do dpkg-deb -I "$deb" | sed -n '/Package:/p;/Version:/p;/Depends:/p'; done
dpkg-deb -c "$OUT"/shairport-sync_*.deb | grep -E 'bin/shairport-sync$'
