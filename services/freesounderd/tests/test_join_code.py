"""Joining a home from a second phone with a code (0.6.19)."""

from __future__ import annotations

from freesounderd.peers import join_code

from .conftest import Lan, settle
from .test_daemon import setup_household


def test_join_code_is_six_digits_and_stable() -> None:
    code = join_code("secret-key")
    assert len(code) == 6 and code.isdigit()
    assert join_code("secret-key") == code and join_code("other-key") != code


async def test_every_player_gives_the_same_code_and_checks_it(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    code_a = (await a.client.get("/api/v2/household/join-code")).json()["code"]
    code_b = (await b.client.get("/api/v2/household/join-code")).json()["code"]
    assert code_a == code_b
    spaced = f"{code_a[:3]} {code_a[3:]}"
    ok = await b.client.post("/api/v2/household/join", json={"code": spaced})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    wrong = "000000" if code_a != "000000" else "111111"
    bad = await a.client.post("/api/v2/household/join", json={"code": wrong})
    assert bad.status_code == 403 and "not right" in bad.json()["detail"]
