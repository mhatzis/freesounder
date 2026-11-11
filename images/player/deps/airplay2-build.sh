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
apt-get install -y --no-install-recommends build-essential devscripts dpkg-dev fakeroot equivs ca-certificates python3
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
		python3 - <<'PY'
import re
from pathlib import Path
p = Path("debian/control")
t = p.read_text()
t = t.replace("debhelper-compat (= 14)", "debhelper-compat (= 13)")
t = re.sub(r"^[ \t]*dh-sequence-single-binary,?[ \t]*\n", "", t, flags=re.M)
t = re.sub(r",?[ \t]*dh-sequence-single-binary", "", t)
# Compat 14 adds ${shlibs:Depends} and ${misc:Depends} implicitly; 13 does not.
paras = t.split("\n\n")
for i, para in enumerate(paras):
    if not para.lstrip().startswith("Package:"):
        continue
    if re.search(r"^Depends:", para, flags=re.M):
        if "${shlibs:Depends}" not in para:
            para = re.sub(r"^Depends:[ \t]*", "Depends: ${shlibs:Depends}, ${misc:Depends}, ", para, count=1, flags=re.M)
    else:
        para = para.rstrip("\n") + "\nDepends: ${shlibs:Depends}, ${misc:Depends}"
    paras[i] = para
t = "\n\n".join(paras)
p.write_text(t)
PY
		cat debian/control
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
# Runtime libraries must be declared, or the image installs a binary that cannot start.
dpkg-deb -f "$OUT"/shairport-sync_*.deb Depends | grep -q libplist
dpkg-deb -f "$OUT"/nqptp_*.deb Depends | grep -q libc6
