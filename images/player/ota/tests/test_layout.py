"""The SD card partition table (layout.env, as make-ab-image.sh writes it) and the first-boot growth that
data-setup --grow-table performs. sfdisk works on plain (sparse) files, so no root is needed."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import OTA_LIB

HERE = Path(__file__).resolve().parent
SFDISK = shutil.which("sfdisk") or shutil.which("sfdisk", path="/usr/sbin:/sbin")
pytestmark = pytest.mark.skipif(SFDISK is None, reason="sfdisk (util-linux fdisk) not installed")
MIB = 1024 * 1024


def layout() -> dict[str, int | str]:
    env = {}
    for line in (HERE.parent / "layout.env").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"')
    return {k: (v if k == "FS_DISK_ID" else int(v)) for k, v in env.items()}


def make_card(path: Path, card_bytes: int) -> dict:
    L = layout()
    p1 = L["FS_ALIGN_MIB"]
    p2 = p1 + L["FS_BOOTSEL_MIB"]
    p3 = p2 + L["FS_BOOT_MIB"]
    p4 = p3 + L["FS_BOOT_MIB"]
    p5 = p4 + L["FS_EBR_GAP_MIB"]
    p6 = p5 + L["FS_ROOT_MIB"] + L["FS_EBR_GAP_MIB"]
    end = p6 + L["FS_ROOT_MIB"]
    s = lambda mib: mib * MIB // 512  # noqa: E731
    script = f"""label: dos
label-id: {L['FS_DISK_ID']}
unit: sectors

start={s(p1)}, size={s(L['FS_BOOTSEL_MIB'])}, type=e
start={s(p2)}, size={s(L['FS_BOOT_MIB'])}, type=c
start={s(p3)}, size={s(L['FS_BOOT_MIB'])}, type=c
start={s(p4)}, size={s(end - p4)}, type=5
start={s(p5)}, size={s(L['FS_ROOT_MIB'])}, type=83
start={s(p6)}, size={s(L['FS_ROOT_MIB'])}, type=83
"""
    with open(path, "wb") as f:
        f.truncate(card_bytes)
    subprocess.run([SFDISK, "-q", str(path)], input=script, text=True, check=True, capture_output=True)
    return table(path)


def table(path: Path) -> dict:
    out = subprocess.run([SFDISK, "-J", str(path)], check=True, capture_output=True, text=True).stdout
    t = json.loads(out)["partitiontable"]
    t["parts"] = {int(p["node"].rsplit("img", 1)[1]): p for p in t["partitions"]}
    return t


def test_layout_matches_fsota_and_system_conf():
    import fsota

    t = layout()
    assert t["FS_DISK_ID"] == "0x" + fsota.DISK_ID
    conf = (OTA_LIB.parents[3] / "etc/rauc/system.conf").read_text()
    for slot, parts in fsota.SLOTS.items():
        assert f"46534f54-0{parts['root']}\ntype=ext4\nbootname={slot}" in conf
        assert f"46534f54-0{parts['boot']}\ntype=vfat" in conf


def test_partitions_are_aligned_and_disjoint(tmp_path):
    t = make_card(tmp_path / "card.img", 16 * 10**9)
    assert t["label"] == "dos" and t["id"] == layout()["FS_DISK_ID"]
    parts = t["parts"]
    assert sorted(parts) == [1, 2, 3, 4, 5, 6]
    align = layout()["FS_ALIGN_MIB"] * MIB // 512
    for n, p in parts.items():
        assert p["start"] % align == 0, f"p{n} not aligned"
    primaries = sorted((parts[n]["start"], parts[n]["start"] + parts[n]["size"]) for n in (1, 2, 3, 4))
    for (_, e1), (s2, _) in zip(primaries, primaries[1:]):
        assert e1 <= s2
    ext = parts[4]
    for n in (5, 6):
        assert ext["start"] < parts[n]["start"] and parts[n]["start"] + parts[n]["size"] <= ext["start"] + ext["size"]
    assert parts[5]["start"] + parts[5]["size"] < parts[6]["start"], "EBR gap before p6"


def test_first_boot_growth_adds_data_after_root_b(tmp_path):
    card = tmp_path / "card.img"
    size = 16 * 10**9
    before = make_card(card, size)
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/sbin:/sbin"}
    subprocess.run(["bash", str(OTA_LIB / "data-setup"), "--grow-table", str(card)], check=True, env=env)
    after = table(card)
    for n in range(1, 7):
        assert after["parts"][n] == before["parts"][n] or n == 4, f"p{n} moved"
    p6, p7, p4 = after["parts"][6], after["parts"][7], after["parts"][4]
    assert p7["start"] > p6["start"] + p6["size"], "p7 must come after root B, not in the EBR gap"
    assert p7["start"] % (8 * 2048) == 0
    assert p4["start"] + p4["size"] == size // 512, "extended partition grown to the end of the card"
    assert p7["start"] + p7["size"] == size // 512
    assert p7["size"] * 512 > 6 * 10**9, "a 16 GB card leaves > 6 GB for /data"


def test_layout_leaves_data_room_on_a_16gb_card():
    t = layout()
    end_mib = t["FS_ALIGN_MIB"] + t["FS_BOOTSEL_MIB"] + 2 * t["FS_BOOT_MIB"] + 2 * t["FS_EBR_GAP_MIB"] + 2 * t["FS_ROOT_MIB"]
    assert end_mib * MIB + t["FS_DATA_MIN_MIB"] * MIB < 16 * 10**9
