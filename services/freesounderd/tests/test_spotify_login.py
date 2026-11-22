"""Speaker Spotify sign-in (0.6.18).

Reported on 0.6.17: sign-in sometimes said the account needs Premium (it is
Premium). Every failure said so, and every attempt first wiped the speaker's
working login.
"""

from __future__ import annotations

import json
from pathlib import Path

from freesounderd.spotify import (
    clear_credentials,
    login_failure_reason,
    restore_credentials,
    saved_credentials,
    stored_username,
)


def test_only_a_real_premium_error_mentions_premium() -> None:
    assert "Premium" in login_failure_reason(
        "failed authenticating with AP: PremiumAccountRequired", "Office"
    )
    timeout = login_failure_reason("starting\\nconnected to AP\\n", "Office")
    assert "Premium" not in timeout and "did not finish" in timeout
    refused = login_failure_reason("login failed: BadCredentials", "Office")
    assert "Premium" not in refused and "sign in again" in refused
    offline = login_failure_reason("dial tcp: lookup apresolve.spotify.com: no such host", "Office")
    assert "Premium" not in offline and "could not reach Spotify" in offline


def test_a_failed_sign_in_can_put_the_old_login_back(tmp_path: Path) -> None:
    state = {"device_id": "dev1", "credentials": {"username": "me", "data": "c2VjcmV0"}}
    (tmp_path / "state.json").write_text(json.dumps(state))
    backup = saved_credentials(tmp_path)
    assert backup == {"username": "me", "data": "c2VjcmV0"}
    clear_credentials(tmp_path)
    assert stored_username(tmp_path) is None and saved_credentials(tmp_path) is None
    restore_credentials(tmp_path, backup)
    data = json.loads((tmp_path / "state.json").read_text())
    assert data["device_id"] == "dev1" and stored_username(tmp_path) == "me"


def test_no_stored_login_means_nothing_to_restore(tmp_path: Path) -> None:
    assert saved_credentials(tmp_path) is None
    empty = {"credentials": {"username": "", "data": None}}
    (tmp_path / "state.json").write_text(json.dumps(empty))
    assert saved_credentials(tmp_path) is None


def test_login5_invalid_credentials_points_to_the_spotify_app() -> None:
    log = (
        'level=fatal msg="daemon exited with error" error="failed authenticating with '
        'login5: failed authenticating with login5: INVALID_CREDENTIALS"'
    )
    reason = login_failure_reason(log, "Lounge")
    assert "Premium" not in reason and "pick Lounge" in reason
