from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fusion_reader_v2.tts import AudioArtifact, AudioCache


def write_wav(path: Path, payload: bytes = b"audio") -> None:
    path.write_bytes(b"RIFF" + len(payload).to_bytes(4, "little") + b"WAVE" + payload)


class AudioCachePolicyTests(unittest.TestCase):
    def test_put_is_atomic_and_rejects_invalid_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = AudioCache(root / "cache")
            invalid = root / "invalid.wav"
            invalid.write_bytes(b"not-wave")
            result = cache.put("invalid", "voice", "es", AudioArtifact(True, path=invalid, provider="fake"))
            self.assertEqual(result.path, invalid)
            self.assertIsNone(cache.get("invalid", "voice", "es"))

            valid = root / "valid.wav"
            write_wav(valid)
            stored = cache.put("valid", "voice", "es", AudioArtifact(True, path=valid, provider="fake"))
            self.assertTrue(stored.path.is_file())
            self.assertTrue(cache.get("valid", "voice", "es").cached)
            self.assertEqual(list(cache.root.glob("*.tmp")), [])

    def test_dry_run_never_deletes_and_apply_stays_inside_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = AudioCache(root / "cache", max_bytes=20, max_age_days=1)
            old_source = root / "old.wav"
            new_source = root / "new.wav"
            write_wav(old_source, b"old-audio")
            write_wav(new_source, b"new-audio")
            old = cache.put("old", "voice", "es", AudioArtifact(True, path=old_source, provider="fake")).path
            new = cache.put("new", "voice", "es", AudioArtifact(True, path=new_source, provider="fake")).path
            now = 2_000_000_000.0
            os.utime(old, (now - 3 * 86400, now - 3 * 86400))
            os.utime(new, (now, now))
            outside = root / "user-export.wav"
            write_wav(outside)

            dry_run = cache.prune(apply=False, now=now)
            self.assertGreaterEqual(dry_run["selected_items"], 1)
            self.assertTrue(old.exists())
            applied = cache.prune(apply=True, now=now)
            self.assertGreaterEqual(applied["removed_items"], 1)
            self.assertFalse(old.exists())
            self.assertTrue(outside.exists())

    def test_symlink_is_never_served_or_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = AudioCache(root / "cache", max_bytes=1)
            outside = root / "outside.wav"
            write_wav(outside)
            target = cache.path_for("linked", "voice", "es")
            target.symlink_to(outside)
            self.assertIsNone(cache.get("linked", "voice", "es"))
            cache.prune(apply=True)
            self.assertTrue(target.is_symlink())
            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
