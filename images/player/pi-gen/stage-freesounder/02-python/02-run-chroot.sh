#!/bin/bash -e
# Build libasound_module_pcm_fsdsp.so (FreeSounder live DSP) for this board,
# then remove exactly the build packages this step pulled in.
export DEBIAN_FRONTEND=noninteractive
dpkg-query -W -f='${Package}\n' | sort > /tmp/fsdsp-before
apt-get install -y --no-install-recommends gcc libc6-dev libasound2-dev
LIBDIR=/usr/lib/$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || gcc -print-multiarch)/alsa-lib
install -d "$LIBDIR"
gcc -O2 -Wall -Wextra -fPIC -DPIC -shared -o "$LIBDIR/libasound_module_pcm_fsdsp.so" \
	/opt/freesounder/fsdsp/fsdsp.c -lasound -lm
chmod 0644 "$LIBDIR/libasound_module_pcm_fsdsp.so"
dpkg-query -W -f='${Package}\n' | sort > /tmp/fsdsp-after
NEW=$(comm -13 /tmp/fsdsp-before /tmp/fsdsp-after | tr '\n' ' ')
if [ -n "$NEW" ]; then
	# shellcheck disable=SC2086
	apt-get purge -y $NEW
fi
rm -f /tmp/fsdsp-before /tmp/fsdsp-after
ls -la "$LIBDIR/libasound_module_pcm_fsdsp.so"
