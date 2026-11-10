"""Format the boot FAT with official-style hidden-sector / geometry values.

When mkfs.fat runs on an offset loop device it records hidden sectors=0 and
a guessed CHS geometry. Official Raspberry Pi OS Lite images record
hidden=partition_start/512 and 128 heads / 63 sectors. The Pi 5 EEPROM
reads this BPB; a mismatch is a suspected cause of a solid ACT LED with
no kernel load.
"""
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
old = 'mkdosfs -n bootfs -F "$FAT_SIZE" "${CLUSTER_OPTS[@]}" -v "$BOOT_DEV" > /dev/null\n'
new = (
    '# Match official Raspberry Pi OS Lite FAT BPB (hidden sectors + CHS).\n'
    '# BOOT_PART_START is in bytes.\n'
    'HIDDEN_SECTORS=$((BOOT_PART_START / 512))\n'
    'mkdosfs -n bootfs -F "$FAT_SIZE" "${CLUSTER_OPTS[@]}" \\\n'
    '\t-h "$HIDDEN_SECTORS" -g 128/63 -v "$BOOT_DEV" > /dev/null\n'
)
if "HIDDEN_SECTORS=" in text:
    print("prerun fat geometry already patched")
elif old not in text:
    raise SystemExit("prerun mkdosfs needle missing")
else:
    p.write_text(text.replace(old, new, 1))
    print("prerun fat geometry patched")
