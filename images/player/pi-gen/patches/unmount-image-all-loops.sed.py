"""Patch pi-gen unmount_image to detach every loop backing the image file."""
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
old = '''	LOOP_DEVICE=$(losetup -n -O NAME -j "$1")
	if [ -n "$LOOP_DEVICE" ]; then
		for part in "$LOOP_DEVICE"p*; do
			if DIR=$(findmnt -n -o target -S "$part"); then
				unmount "$DIR"
			fi
		done
		losetup -d "$LOOP_DEVICE"
	fi
'''
new = '''	# Detach every loop backed by this image (whole-disk and offset
	# partition loops used when loop.max_part=0).
	while read -r LOOP_DEVICE; do
		[ -z "$LOOP_DEVICE" ] && continue
		if DIR=$(findmnt -n -o target -S "$LOOP_DEVICE"); then
			unmount "$DIR"
		fi
		for part in "$LOOP_DEVICE"p*; do
			if [ -b "$part" ] && DIR=$(findmnt -n -o target -S "$part"); then
				unmount "$DIR"
			fi
		done
		losetup -d "$LOOP_DEVICE" || true
	done < <(losetup -n -O NAME -j "$1")
'''
if 'offset partition loops used when loop.max_part' in text:
    print('unmount_image already patched')
elif old not in text:
    raise SystemExit('unmount_image needle missing')
else:
    p.write_text(text.replace(old, new, 1))
    print('unmount_image patched')
