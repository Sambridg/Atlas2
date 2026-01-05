from pathlib import Path

from caal.audio_store import AudioStore


def test_audio_store_add_and_get(tmp_path):
    audio_path = tmp_path / "clip.opus"
    audio_path.write_bytes(b"audio-bytes")
    store = AudioStore(tmp_path / "audio.db")

    meta = store.add_artifact(path=audio_path, retention_days=1, codec="opus", sample_rate=48000)

    assert meta["audio_id"]
    assert meta["path"] == str(audio_path)
    assert meta["codec"] == "opus"
    assert meta["sample_rate"] == 48000
    assert meta["sha256"]


def test_audio_store_cleanup(tmp_path):
    audio_path = tmp_path / "clip.opus"
    audio_path.write_bytes(b"audio-bytes")
    store = AudioStore(tmp_path / "audio.db")

    meta = store.add_artifact(path=audio_path, retention_days=0, pinned=False)
    assert store.get_artifact(meta["audio_id"]) is not None

    deleted = store.cleanup_expired()
    assert deleted >= 1
    assert store.get_artifact(meta["audio_id"]) is None
