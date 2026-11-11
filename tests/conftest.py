"""Suite-wide test settings."""

import pytest


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    """A fixed analysis-signing key, so no test writes a key file into data/.

    Tests of the key file itself delete this variable.
    """
    monkeypatch.setenv("AVATAR_SIGNING_KEY", "test-signing-key-" + "x" * 32)
