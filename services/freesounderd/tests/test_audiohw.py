"""Audio board detection and the config.txt DAC block (freesounderd.audiohw)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freesounderd import audiohw
from freesounderd.audiohw import BLOCK_END, BLOCK_START, block_overlay, decide, run_boot, set_block

# Raspberry Pi OS trixie config.txt tail: model sections before our block.
STOCK = """dtparam=audio=on
dtoverlay=vc4-kms-v3d
max_framebuffers=2
arm_64bit=1

[cm4]
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[pi5]
dtoverlay=nospi10
"""
OLD_BLOCK = (
    "\n# --- freesounder-dac-start ---\n"
    "# Set from the FreeSounder app (room sound settings: hifiberry-dacplus).\n"
    "dtoverlay=hifiberry-dacplus\n"
    "# --- freesounder-dac-end ---\n"
)
PI4 = (
    " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
    "                      bcm2835 Headphones\n"
    " 1 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n"
    " 2 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1\n"
)
PIFI = " 3 [sndrpihifiberry]: RPi-simple - snd_rpi_hifiberry_dacplus\n"
USB = " 3 [Device         ]: USB-Audio - USB Audio Device\n"


def _block(text: str) -> str:
    return text[text.index(BLOCK_START) : text.index(BLOCK_END)]


def test_block_is_under_all_and_keeps_onboard_audio():
    text = set_block(STOCK, "hifiberry-dacplus", "test")
    block = _block(text)
    # Without [all] the overlay would only apply to the last section ([pi5]).
    assert block.splitlines()[1] == "[all]"
    assert "dtparam=audio=on" in block
    assert block_overlay(text) == "hifiberry-dacplus"
    assert text.startswith(STOCK)


def test_old_block_replaced_and_audio_off_turned_on():
    text = set_block(STOCK.replace("audio=on", "audio=off") + OLD_BLOCK, "wm8960-soundcard", "x")
    assert text.count(BLOCK_START) == 1
    assert "dtparam=audio=off" not in text
    assert block_overlay(text) == "wm8960-soundcard"
    assert set_block(text, "wm8960-soundcard", "x") == text


def cards(text: str) -> list[dict[str, str]]:
    path = Path("/tmp") / "fs-cards-test"
    path.write_text(text)
    return audiohw.read_cards(path)


def probe_found(*addrs: int):
    return lambda: (set(addrs), "")


def test_pifi_dac_detected_gets_hifiberry_dacplus_std():
    new, det = decide(
        choice="auto", config=STOCK, cards=cards(PI4), hat=None, probe=probe_found(0x4D), state={}
    )
    assert block_overlay(new) == "hifiberry-dacplus-std"
    assert det.action == "reboot"
    assert "PiFi DAC+ 2.0" in det.detected
    assert det.expect_card


@pytest.mark.parametrize(
    ("addr", "overlay"),
    [(0x4C, "iqaudio-dacplus"), (0x1A, "wm8960-soundcard"), (0x1B, "hifiberry-amp")],
)
def test_other_codecs(addr, overlay):
    new, det = decide(
        choice="auto", config=STOCK, cards=cards(PI4), hat=None, probe=probe_found(addr), state={}
    )
    assert block_overlay(new) == overlay
    assert det.action == "reboot"


def test_nothing_on_i2c_uses_onboard_without_reboot():
    new, det = decide(
        choice="auto", config=STOCK, cards=cards(PI4), hat=None, probe=probe_found(), state={}
    )
    assert block_overlay(new) is None
    assert det.action == "none"
    assert "no audio board found" in det.detected


def test_eeprom_hat_is_left_to_the_firmware():
    called = []
    new, det = decide(
        choice="auto",
        config=STOCK,
        cards=cards(PI4),
        hat="HiFiBerry DAC+ Pro",
        probe=lambda: called.append(1) or (set(), ""),
        state={},
    )
    assert not called
    assert block_overlay(new) is None
    assert "EEPROM" in det.detected


def test_working_card_after_reboot_is_reported():
    config = set_block(STOCK, "hifiberry-dacplus", "auto")
    state = {
        "board": "PCM512x at 0x4d",
        "pending": True,
        "tried": ["hifiberry-dacplus"],
        "reboots": 1,
    }
    new, det = decide(
        choice="auto",
        config=config,
        cards=cards(PI4 + PIFI),
        hat=None,
        probe=probe_found(0x4D),
        state=state,
    )
    assert new == config
    assert det.action == "none"
    assert "sndrpihifiberry" in det.detected


def test_card_missing_tries_plain_then_gives_up():
    config = set_block(STOCK, "hifiberry-dacplus-std", "auto")
    state = {"board": "x", "pending": True, "tried": ["hifiberry-dacplus-std"], "reboots": 1}
    new, det = decide(
        choice="auto",
        config=config,
        cards=cards(PI4),
        hat=None,
        probe=probe_found(0x4D),
        state=state,
    )
    assert block_overlay(new) == "hifiberry-dacplus"
    assert det.action == "reboot"
    state = {
        "board": "x",
        "pending": True,
        "tried": ["hifiberry-dacplus-std", "hifiberry-dacplus"],
        "reboots": 2,
    }
    new2, det2 = decide(
        choice="auto", config=new, cards=cards(PI4), hat=None, probe=probe_found(0x4D), state=state
    )
    assert det2.action == "gave-up"
    assert new2 == new


def test_pifi_first_probe_gets_std_overlay():
    new, det = decide(
        choice="auto", config=STOCK, cards=cards(PI4), hat=None, probe=probe_found(0x4D), state={}
    )
    assert block_overlay(new) == "hifiberry-dacplus-std"
    assert det.action == "reboot"


def test_legacy_plain_overlay_switches_to_std_once():
    from freesounderd.audiohw import CODECS

    config = set_block(STOCK, "hifiberry-dacplus", "auto")
    state = {
        "board": CODECS[0].label,
        "pending": False,
        "tried": ["hifiberry-dacplus"],
        "reboots": 0,
    }
    new, det = decide(
        choice="auto",
        config=config,
        cards=cards(PI4 + PIFI),
        hat=None,
        probe=probe_found(),
        state=state,
    )
    assert block_overlay(new) == "hifiberry-dacplus-std"
    assert det.action == "reboot"
    # Once std was tried it is never flipped back automatically.
    state2 = {"board": CODECS[0].label, "tried": ["hifiberry-dacplus", "hifiberry-dacplus-std"]}
    new2, det2 = decide(
        choice="auto",
        config=new,
        cards=cards(PI4 + PIFI),
        hat=None,
        probe=probe_found(),
        state=state2,
    )
    assert new2 == new
    assert det2.action == "none"


def test_usb_only_still_probes_for_a_board():
    new, det = decide(
        choice="auto", config=STOCK, cards=cards(PI4 + USB), hat=None, probe=probe_found(), state={}
    )
    assert "USB audio Device" in det.detected
    assert det.action == "none"


def test_manual_choice_writes_overlay_and_reboots_at_most_once():
    new, det = decide(
        choice="pifi-dacplus",
        config=STOCK + OLD_BLOCK,
        cards=cards(PI4),
        hat=None,
        probe=probe_found(),
        state={},
    )
    assert block_overlay(new) == "hifiberry-dacplus-std"
    assert "[all]" in _block(new)
    assert det.action == "reboot"
    again, det2 = decide(
        choice="pifi-dacplus",
        config=new,
        cards=cards(PI4),
        hat=None,
        probe=probe_found(),
        state={"reboots": 1},
    )
    assert again == new
    assert det2.action == "none"
    assert det2.notes


def _boot(tmp_path: Path, cards_text: str, found: set[int], dac: str = "auto") -> tuple[dict, list]:
    (tmp_path / "config.txt").write_text(STOCK)
    (tmp_path / "dac").write_text(dac + "\n")
    (tmp_path / "cards").write_text(cards_text)
    reboots: list[int] = []
    run_boot(
        config_path=tmp_path / "config.txt",
        dac_path=tmp_path / "dac",
        state_path=tmp_path / "state.json",
        status_path=tmp_path / "status.json",
        cards_path=tmp_path / "cards",
        hat_path=tmp_path / "no-hat",
        probe=lambda: (found, ""),
        reboot=lambda: reboots.append(1),
        settle=0,
    )
    return json.loads((tmp_path / "status.json").read_text()), reboots


def test_boot_reboots_once_then_settles(tmp_path, monkeypatch):
    monkeypatch.setattr(audiohw, "SHORT_BOOTS", tmp_path / "short-boots")
    (tmp_path / "short-boots").write_text("2\n")
    status, reboots = _boot(tmp_path, PI4, {0x4D})
    assert reboots == [1]
    assert status["overlay"] == "hifiberry-dacplus-std"
    # The intentional reboot is not a short power cycle (factory reset counter).
    assert (tmp_path / "short-boots").read_text() == "0\n"
    # Next boot: the card is there; no more reboots, the board is remembered.
    (tmp_path / "cards").write_text(PI4 + PIFI)
    run_boot(
        config_path=tmp_path / "config.txt",
        dac_path=tmp_path / "dac",
        state_path=tmp_path / "state.json",
        status_path=tmp_path / "status.json",
        cards_path=tmp_path / "cards",
        hat_path=tmp_path / "no-hat",
        probe=lambda: ({0x4D}, ""),
        reboot=lambda: reboots.append(1),
        settle=0,
    )
    assert reboots == [1]
    status = json.loads((tmp_path / "status.json").read_text())
    assert "sndrpihifiberry" in status["detected"]
    assert json.loads((tmp_path / "state.json").read_text())["pending"] is False


def test_boot_never_loops(tmp_path, monkeypatch):
    monkeypatch.setattr(audiohw, "SHORT_BOOTS", tmp_path / "short-boots")
    reboots: list[int] = []
    (tmp_path / "config.txt").write_text(STOCK)
    (tmp_path / "dac").write_text("auto\n")
    (tmp_path / "cards").write_text(PI4)
    for _ in range(6):
        run_boot(
            config_path=tmp_path / "config.txt",
            dac_path=tmp_path / "dac",
            state_path=tmp_path / "state.json",
            status_path=tmp_path / "status.json",
            cards_path=tmp_path / "cards",
            hat_path=tmp_path / "no-hat",
            probe=lambda: ({0x4D}, ""),
            reboot=lambda: reboots.append(1),
            settle=0,
        )
    assert len(reboots) == audiohw.MAX_REBOOTS


def test_set_from_app(tmp_path):
    cfg = tmp_path / "config.txt"
    cfg.write_text(STOCK + OLD_BLOCK)
    assert audiohw.run_set("pifi-dacplus", config_path=cfg, dac_path=tmp_path / "dac") == 0
    assert block_overlay(cfg.read_text()) == "hifiberry-dacplus-std"
    assert (tmp_path / "dac").read_text() == "pifi-dacplus\n"
    assert audiohw.run_set("bogus", config_path=cfg, dac_path=tmp_path / "dac") == 1


def test_every_app_choice_has_an_overlay():
    from freesounderd.models import DAC_CHOICES

    assert set(DAC_CHOICES) - {"auto", "none"} == set(audiohw.BOARDS)
