from __future__ import annotations

import os
import pickle
from pathlib import Path

import pytest

from src.security.artifact_integrity import (
    ArtifactIntegrityError,
    TRUSTED_REPOSITORY_ARTIFACTS,
    load_verified_pickle,
    read_verified_artifact,
    write_signed_artifact,
)


def test_all_pinned_repository_artifacts_match_the_manifest():
    for relative_path in TRUSTED_REPOSITORY_ARTIFACTS:
        assert read_verified_artifact(Path(relative_path))


def test_signed_runtime_artifact_round_trip_and_tamper_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("FENIX_MODEL_SIGNING_KEY", "test-model-key-" + ("x" * 48))
    path = tmp_path / "runtime.pkl"
    payload = pickle.dumps({"model": "known-good"})

    write_signed_artifact(path, payload)

    assert load_verified_pickle(path) == {"model": "known-good"}
    assert path.stat().st_mode & 0o077 == 0
    assert path.with_name("runtime.pkl.hmac").stat().st_mode & 0o077 == 0

    path.write_bytes(payload + b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        load_verified_pickle(path)


def test_unsigned_pickle_is_rejected_before_deserialization(tmp_path, monkeypatch):
    marker = tmp_path / "executed"

    class Malicious:
        def __reduce__(self):
            return (os.system, (f"touch {marker}",))

    path = tmp_path / "untrusted.pkl"
    path.write_bytes(pickle.dumps(Malicious()))
    monkeypatch.delenv("FENIX_MODEL_SIGNING_KEY", raising=False)
    monkeypatch.setenv("FENIX_MODEL_SIGNING_KEY_FILE", str(tmp_path / "missing-key"))

    with pytest.raises(ArtifactIntegrityError):
        load_verified_pickle(path)

    assert not marker.exists()
