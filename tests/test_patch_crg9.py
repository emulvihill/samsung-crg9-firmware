"""Integration tests use an independently downloaded, untracked Samsung image."""
import hashlib
import errno
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "patch_crg9_dp2.py"
sys.path.insert(0, str(ROOT))
import patch_crg9_dp2 as patcher
SOURCE_SHA = "8fcc3acf15f2988e2ff7ac025a23c7c7b2df69d1a6fa2c2425dc33ad28b38ec8"
PRIOR_PATCHED_MAIN_SHA = "7eea04350d04483ebf7e05a6499a610b561920dd8cf6156aa249ddf981e7ea95"
PATCHED_MAIN_SHA = "fafa1d58d8b2317f53ae172ea717d8a7fba911491d6071b632d7a019b03ea69f"
VERSION_OFFSET = 0x2070ac
ORIGINAL_VERSION = b"m-RG949CCAA-1007.2\0"
CANDIDATE_VERSION = b"m-RG949CCAA-1007.3\0"
CODE_REPLACEMENTS = (
    (0x69e0b, bytes.fromhex("d0e9f5c6c0e00941f8c7fe1f"),
     bytes.fromhex("d0ea0682d0e9f5a690ca9002")),
    (0x69ed7, bytes.fromhex("98c3c0e00941f8c7fe1fe7ffa60a2ffdde"),
     bytes.fromhex("98c3900498c2c0e00941f8c7fe1f2fff32")),
)


def crc_reference(data, width, polynomial):
    """Independent bit-at-a-time check, intentionally unlike the patcher table."""
    result = 0
    mask = (1 << width) - 1
    for byte in data:
        result ^= byte << (width - 8)
        for _ in range(8):
            top = result & (1 << (width - 1))
            result = ((result << 1) ^ (polynomial if top else 0)) & mask
    return result


class PatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(os.environ.get("CRG9_FIRMWARE", ROOT / "firmware/m-RG949CCAA-1007.2[2D8D].bin"))
        if not path.is_file():
            raise unittest.SkipTest("Set CRG9_FIRMWARE to the original Samsung CCAA 1007.2 BIN")
        cls.original = path.read_bytes()
        if hashlib.sha256(cls.original).hexdigest() != SOURCE_SHA:
            raise AssertionError("Integration fixture is not the exact Samsung original")
        cls.pa_path = Path(os.environ.get(
            "CRG9_PA_FIRMWARE", path.with_name("m-RG949CCPA-1004.6[A294].bin")))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "original.bin"
        self.source.write_bytes(self.original)
        self.output = self.directory / "patched.bin"

    def run_cli(self, source=None, output=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(source or self.source), str(output or self.output)],
            capture_output=True, text=True, timeout=30,
        )

    def test_exact_input_writes_only_the_intended_program_change(self):
        run = self.run_cli()
        self.assertEqual(run.returncode, 0, run.stderr)
        result = self.output.read_bytes()
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(len(result), len(self.original))
        end = struct.unpack_from("<I", result, 0x1000c)[0]
        dec = zlib.decompressobj(-15)
        main = dec.decompress(result[0x33880:end])
        self.assertTrue(dec.eof)
        self.assertEqual(len(dec.unused_data), 6)
        self.assertEqual(struct.unpack_from("<I", dec.unused_data)[0], len(main))
        self.assertEqual(hashlib.sha256(main).hexdigest(), PATCHED_MAIN_SHA)
        old = zlib.decompress(self.original[0x33880:], -15)
        changes = {i for i, (a, b) in enumerate(zip(old, main)) if a != b}
        allowed = (set(range(0x69e0b, 0x69e17)) | set(range(0x69ed7, 0x69ee8)) |
                   {VERSION_OFFSET + len(ORIGINAL_VERSION) - 2})
        self.assertTrue(changes)
        self.assertLessEqual(changes, allowed)
        self.assertEqual(len(main), len(old))
        self.assertLessEqual(end, 0x14d483)
        self.assertEqual(struct.unpack_from("<I", result, 0x10008)[0], 0x200000 + end - 0x30080)
        self.assertEqual(struct.unpack_from("<I", result, 0x311ca)[0], end - 0x30080)
        ranges = [(0x10008, 0x10010), (0x10014, 0x10018), (0x311ca, 0x311ce),
                  (0x33880, 0x14d488), (0x2c60b0, 0x2c60b4)]
        cursor = 0
        for start, stop in ranges:
            self.assertEqual(result[cursor:start], self.original[cursor:start])
            cursor = stop
        self.assertEqual(result[cursor:], self.original[cursor:])
        pre_crc = bytearray(result[0x10000:end - 2])
        pre_crc[0x14:0x18] = bytes(4)
        self.assertEqual(crc_reference(pre_crc, 16, 0x8005), int.from_bytes(result[end - 2:end], "big"))
        self.assertEqual(crc_reference(result[0x30080:end], 32, 0x04c11db7),
                         struct.unpack_from("<I", result, 0x10014)[0])
        self.assertEqual(crc_reference(result[:0x2c60b0], 32, 0x04c11db7),
                         int.from_bytes(result[0x2c60b0:0x2c60b4], "big"))

    def test_candidate_changes_only_the_version_digit_beyond_the_prior_candidate(self):
        original_main = zlib.decompress(self.original[0x33880:], -15)
        self.assertEqual(
            original_main[VERSION_OFFSET:VERSION_OFFSET + len(ORIGINAL_VERSION)],
            ORIGINAL_VERSION,
        )
        prior_candidate = bytearray(original_main)
        for offset, before, after in CODE_REPLACEMENTS:
            self.assertEqual(prior_candidate[offset:offset + len(before)], before)
            prior_candidate[offset:offset + len(before)] = after
        self.assertEqual(hashlib.sha256(prior_candidate).hexdigest(), PRIOR_PATCHED_MAIN_SHA)

        candidate = patcher.extract_main(patcher.patch_bytes(self.original))
        self.assertEqual(
            candidate[VERSION_OFFSET:VERSION_OFFSET + len(CANDIDATE_VERSION)],
            CANDIDATE_VERSION,
        )
        changes = {i for i, (before, after) in enumerate(zip(prior_candidate, candidate))
                   if before != after}
        self.assertEqual(changes, {VERSION_OFFSET + len(ORIGINAL_VERSION) - 2})

    def test_wrong_hash_and_truncation_create_no_output(self):
        for data in (self.original[:-1], bytes(len(self.original)),
                     self.original[:500000] + bytes([self.original[500000] ^ 1]) + self.original[500001:]):
            with self.subTest(size=len(data)):
                self.source.write_bytes(data)
                run = self.run_cli()
                self.assertNotEqual(run.returncode, 0)
                self.assertIn("SHA-256", run.stderr)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.source.read_bytes(), data)

    def test_existing_output_and_input_path_are_not_overwritten(self):
        self.output.write_bytes(b"keep this")
        run = self.run_cli()
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b"keep this")
        run = self.run_cli(output=self.source)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_actual_ccpa_family_is_refused_without_output(self):
        if not self.pa_path.is_file():
            self.skipTest("Set CRG9_PA_FIRMWARE to the CCPA BIN from the same Samsung archive")
        wrong_family = self.pa_path.read_bytes()
        self.assertEqual(hashlib.sha256(wrong_family).hexdigest(),
                         "1cec199ac2ebda949fda74394628f27814ceab0d131e0be30eaae556db9e5a19")
        self.source.write_bytes(wrong_family)
        run = self.run_cli()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("SHA-256", run.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), wrong_family)

    def test_repeated_runs_are_identical_on_one_runtime(self):
        one = self.run_cli()
        self.assertEqual(one.returncode, 0, one.stderr)
        other = self.directory / "second.bin"
        two = self.run_cli(output=other)
        self.assertEqual(two.returncode, 0, two.stderr)
        self.assertEqual(other.read_bytes(), self.output.read_bytes())

    def test_default_name_uses_the_computed_checksum(self):
        run = subprocess.run([sys.executable, str(SCRIPT), str(self.source)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        outputs = list(self.directory.glob("m-RG949CCAA-1007.3[[]*].bin"))
        self.assertEqual(len(outputs), 1)
        checksum = sum(outputs[0].read_bytes()[:0x2c60b4]) & 0xffff
        self.assertEqual(outputs[0].name, f"m-RG949CCAA-1007.3[{checksum:04X}].bin")

    def test_a_larger_valid_compression_stream_is_refused(self):
        original_compressor = zlib.compressobj
        def stored_blocks(*args, **kwargs):
            return original_compressor(0, zlib.DEFLATED, -15)
        with mock.patch.object(patcher.zlib, "compressobj", side_effect=stored_blocks):
            with self.assertRaisesRegex(ValueError, "does not fit"):
                patcher.patch_bytes(self.original)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(self.output.exists())

    def test_atomic_publication_cannot_overwrite_a_racing_file(self):
        real_link = os.link
        def concurrent_creator(source, destination):
            Path(destination).write_bytes(b"created concurrently")
            return real_link(source, destination)
        with mock.patch.object(patcher.os, "link", side_effect=concurrent_creator):
            with self.assertRaises(FileExistsError):
                patcher.write_new_file(self.output, b"new image")
        self.assertEqual(self.output.read_bytes(), b"created concurrently")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_filesystems_without_hard_links_can_create_a_new_output(self):
        with mock.patch.object(patcher.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "not supported")):
            patcher.write_new_file(self.output, b"verified image")
        self.assertEqual(self.output.read_bytes(), b"verified image")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_fallback_never_overwrites_an_existing_output(self):
        self.output.write_bytes(b"existing image")
        with mock.patch.object(patcher.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "not supported")):
            with self.assertRaises(FileExistsError):
                patcher.write_new_file(self.output, b"replacement")
        self.assertEqual(self.output.read_bytes(), b"existing image")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_real_filesystem_errors_are_reported_without_fallback(self):
        for error_number in (errno.EIO, errno.ENOSPC, errno.EROFS):
            with self.subTest(errno=error_number):
                with mock.patch.object(patcher.os, "link", side_effect=OSError(error_number, "filesystem failure")):
                    with self.assertRaises(OSError) as raised:
                        patcher.write_new_file(self.output, b"image")
                self.assertEqual(raised.exception.errno, error_number)
                self.assertFalse(self.output.exists())
                self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_windows_unsupported_links_use_exclusive_fallback(self):
        for windows_error in (1, 50):
            with self.subTest(winerror=windows_error):
                destination = self.directory / f"windows-{windows_error}.bin"
                error = OSError(errno.EINVAL, "unsupported Windows filesystem operation")
                error.winerror = windows_error
                with mock.patch.object(patcher.os, "link", side_effect=error):
                    patcher.write_new_file(destination, b"image")
                self.assertEqual(destination.read_bytes(), b"image")

    def test_missing_platform_errno_name_does_not_break_fallback(self):
        error = OSError(errno.EPERM, "hard links not permitted")
        with mock.patch.object(patcher, "errno", object()):
            with mock.patch.object(patcher.os, "link", side_effect=error):
                patcher.write_new_file(self.output, b"image")
        self.assertEqual(self.output.read_bytes(), b"image")

    def test_fallback_removes_its_own_output_if_writing_fails(self):
        real_fsync = os.fsync
        calls = 0
        def fail_destination_sync(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.EIO, "simulated write failure")
            return real_fsync(fd)
        with mock.patch.object(patcher.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "not supported")):
            with mock.patch.object(patcher.os, "fsync", side_effect=fail_destination_sync):
                with self.assertRaises(OSError):
                    patcher.write_new_file(self.output, b"image")
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.directory.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
