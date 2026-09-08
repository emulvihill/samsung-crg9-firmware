import hashlib
import json
import os
import time
import tempfile
import unittest
from pathlib import Path

import patch_crg9_dp2 as patcher

from aeon_block import (
    CALL_TARGET,
    ENTRY,
    ORIGINAL_BLOCKS,
    OUTLINE_ENTRY,
    SELECTOR_ADDRESS,
    blocks_from_main,
    run,
)


OLD_SEGMENT_1 = bytes.fromhex("d0 e9 f5 c6 c0 e0 09 41 f8 c7 fe 1f")
NEW_SEGMENT_1 = bytes.fromhex("d0 ea 06 82 d0 e9 f5 a6 90 ca 90 02")
OLD_SEGMENT_2 = bytes.fromhex(
    "98 c3 c0 e0 09 41 f8 c7 fe 1f e7 ff a6 0a 2f fd de"
)
NEW_SEGMENT_2 = bytes.fromhex(
    "98 c3 90 04 98 c2 c0 e0 09 41 f8 c7 fe 1f 2f ff 32"
)

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "firmware/m-RG949CCAA-1007.2[2D8D].bin"
MATRIX_LOG = ROOT / "analysis/actual-candidate-matrix.jsonl"
MATRIX_PROTOCOL = 1


def completed_groups(path, identity):
    completed = set()
    malformed = 0
    if not path.exists():
        return completed, malformed
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if (
            record.get("kind") == "input"
            and all(record.get(key) == value for key, value in identity.items())
            and record.get("pairs") == 256
            and record.get("status") == "pass"
            and record.get("input") in range(256)
        ):
            completed.add(record["input"])
    return completed, malformed


def candidate_blocks():
    primary = ORIGINAL_BLOCKS[ENTRY]
    old_start = 0x26D60B - ENTRY
    old_end = old_start + len(OLD_SEGMENT_1)
    if primary[old_start:old_end] != OLD_SEGMENT_1:
        raise AssertionError("embedded primary block does not contain expected original")
    return {
        ENTRY: primary[:old_start] + NEW_SEGMENT_1 + primary[old_end:],
        OUTLINE_ENTRY: NEW_SEGMENT_2,
    }


class CandidateInputBlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(os.environ.get("CRG9_FIRMWARE", FIRMWARE))
        if not path.is_file():
            raise unittest.SkipTest(
                "Set CRG9_FIRMWARE to the original Samsung CCAA 1007.2 BIN"
            )
        cls.original_image = path.read_bytes()
        if patcher.sha256(cls.original_image) != patcher.SOURCE_SHA256:
            raise AssertionError("Integration fixture is not the exact Samsung original")
        cls.original_main = patcher.extract_main(cls.original_image)
        cls.patched_image = patcher.patch_bytes(cls.original_image)
        cls.patched_main = patcher.extract_main(cls.patched_image)
        cls.actual_original_blocks = blocks_from_main(cls.original_main)
        cls.actual_candidate_blocks = blocks_from_main(cls.patched_main)

    def test_00_candidate_replacements_match_the_exact_original_ranges(self):
        blocks = candidate_blocks()
        offset = 0x26D60B - ENTRY

        self.assertEqual(self.actual_original_blocks, ORIGINAL_BLOCKS)
        self.assertEqual(self.actual_candidate_blocks, blocks)
        self.assertEqual(
            patcher.sha256(self.patched_main), patcher.PATCHED_MAIN_SHA256
        )
        self.assertEqual(ORIGINAL_BLOCKS[ENTRY][offset : offset + 12], OLD_SEGMENT_1)
        self.assertEqual(ORIGINAL_BLOCKS[OUTLINE_ENTRY], OLD_SEGMENT_2)
        self.assertEqual(blocks[ENTRY][:offset], ORIGINAL_BLOCKS[ENTRY][:offset])
        self.assertEqual(blocks[ENTRY][offset : offset + 12], NEW_SEGMENT_1)
        self.assertEqual(blocks[ENTRY][offset + 12 :], ORIGINAL_BLOCKS[ENTRY][offset + 12 :])
        self.assertEqual(blocks[OUTLINE_ENTRY], NEW_SEGMENT_2)

    def test_01_dp2_fails_on_original_then_passes_on_candidate(self):
        original = run(ORIGINAL_BLOCKS, 0x0A, 0x7C)
        self.assertEqual((original.selection, original.calls, original.writes), (0x7C, (), ()))

        candidate = run(self.actual_candidate_blocks, 0x0A, 0x7C)
        self.assertEqual(candidate.selection, 2)
        self.assertEqual(candidate.calls, (CALL_TARGET,))
        self.assertEqual(candidate.writes, ((SELECTOR_ADDRESS, 2),))

    def test_02_all_non_dp2_inputs_preserve_original_observable_behavior(self):
        blocks = self.actual_candidate_blocks

        unit_started = time.perf_counter()
        run(blocks, 0x0A, 0)
        unit_elapsed = time.perf_counter() - unit_started

        interpreter_hash = hashlib.sha256(
            (ROOT / "tests/aeon_block.py").read_bytes()
        ).hexdigest()
        validator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        identity = {
            "protocol": MATRIX_PROTOCOL,
            "candidate_main_sha256": patcher.PATCHED_MAIN_SHA256,
            "source_sha256": patcher.SOURCE_SHA256,
            "interpreter_sha256": interpreter_hash,
            "validator_sha256": validator_hash,
        }
        resume_enabled = os.environ.get("CRG9_MATRIX_RESUME") == "1"
        completed, malformed = (
            completed_groups(MATRIX_LOG, identity) if resume_enabled else (set(), 0)
        )

        batch_started = time.perf_counter()
        comparisons = 0
        MATRIX_LOG.parent.mkdir(parents=True, exist_ok=True)
        with MATRIX_LOG.open("a+", encoding="utf-8") as log:
            log.seek(0, 2)
            if log.tell():
                log.seek(log.tell() - 1)
                if log.read(1) != "\n":
                    log.write("\n")
            log.write(
                json.dumps(
                    {
                        "kind": "run",
                        "protocol": MATRIX_PROTOCOL,
                        "candidate_main_sha256": patcher.PATCHED_MAIN_SHA256,
                        "source_sha256": patcher.SOURCE_SHA256,
                        "interpreter_sha256": interpreter_hash,
                        "validator_sha256": validator_hash,
                        "one_evaluation_seconds": unit_elapsed,
                        "resume_enabled": resume_enabled,
                        "completed_input_groups_before_run": len(completed),
                        "ignored_malformed_records": malformed,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            log.flush()

            for value in range(256):
                if value in completed:
                    comparisons += 256
                    continue
                for previous_selector in range(256):
                    original = run(
                        self.actual_original_blocks, value, previous_selector
                    )
                    candidate = run(blocks, value, previous_selector)
                    if value == 0x0A:
                        self.assertEqual(candidate.selection, 2)
                        self.assertEqual(candidate.calls, (CALL_TARGET,))
                        self.assertEqual(
                            candidate.writes, ((SELECTOR_ADDRESS, 2),)
                        )
                    else:
                        self.assertEqual(
                            (
                                candidate.selection,
                                candidate.calls,
                                candidate.writes,
                            ),
                            (original.selection, original.calls, original.writes),
                        )
                    comparisons += 1
                log.write(
                    json.dumps(
                        {
                            "kind": "input",
                            "protocol": MATRIX_PROTOCOL,
                            "candidate_main_sha256": patcher.PATCHED_MAIN_SHA256,
                            "source_sha256": patcher.SOURCE_SHA256,
                            "interpreter_sha256": interpreter_hash,
                            "validator_sha256": validator_hash,
                            "input": value,
                            "pairs": 256,
                            "status": "pass",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                log.flush()
        batch_elapsed = time.perf_counter() - batch_started

        self.assertEqual(comparisons, 256 * 256)
        print(
            f"one evaluation: {unit_elapsed:.6f}s; "
            f"full 65,536-pair batch: {batch_elapsed:.6f}s; "
            f"resumed groups: {len(completed)}"
        )

    def test_03_resume_ignores_a_partial_record_between_valid_segments(self):
        identity = {"protocol": MATRIX_PROTOCOL}
        valid_0 = {
            "kind": "input",
            "protocol": MATRIX_PROTOCOL,
            "input": 0,
            "pairs": 256,
            "status": "pass",
        }
        valid_1 = valid_0 | {"input": 1}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.jsonl"
            path.write_text(
                json.dumps(valid_0)
                + "\n"
                + '{"kind":"input"'
                + "\n"
                + json.dumps(valid_1)
                + "\n"
            )
            completed, malformed = completed_groups(path, identity)
        self.assertEqual(completed, {0, 1})
        self.assertEqual(malformed, 1)


if __name__ == "__main__":
    unittest.main()
