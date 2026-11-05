"""A privileged reader must not follow a candidate's filesystem aliases."""
import tempfile
import unittest
from pathlib import Path

from contracts.trace.payload import ContentAddressedPayloadStore


class PayloadFileBoundaryTests(unittest.TestCase):
    def test_payload_file_symlink_cannot_read_other_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ContentAddressedPayloadStore(root / "payloads")
            ref = store.put({"content": "owned"})
            outside = root / "other-task.json"
            outside.write_text('{"content":"held-out input"}', encoding="utf-8")
            path = store.root / f"{ref.payload_id}.json"
            path.unlink()
            path.symlink_to(outside)
            with self.assertRaises((OSError, ValueError)):
                store.get(ref.payload_id)

    def test_payload_directory_symlink_cannot_read_other_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = ContentAddressedPayloadStore(root / "other-task")
            ref = original.put({"content": "held-out input"})
            alias = root / "own-payloads"
            alias.symlink_to(original.root, target_is_directory=True)
            with self.assertRaises((OSError, ValueError)):
                ContentAddressedPayloadStore(alias).get(ref.payload_id)

    def test_payload_id_cannot_traverse_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "payloads").mkdir()
            (root / "outside.json").write_text('{"content":"private"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                ContentAddressedPayloadStore(root / "payloads").get("../outside")

    def test_payload_content_must_match_its_address(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContentAddressedPayloadStore(Path(directory))
            ref = store.put({"content": "original"})
            (store.root / f"{ref.payload_id}.json").write_text('{"content":"modified"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                store.get(ref.payload_id)
