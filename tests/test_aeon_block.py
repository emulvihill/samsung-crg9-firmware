import os
import unittest
from pathlib import Path

import patch_crg9_dp2 as patcher

from aeon_block import (
    CALL_TARGET,
    ENTRY,
    INPUT_ADDRESS,
    ORIGINAL_BLOCKS,
    OUTLINE_ENTRY,
    RETURN_ADDRESS,
    SELECTOR_ADDRESS,
    InterpretationError,
    blocks_from_main,
    decode_instruction,
    run,
)


class DecodeInstructionTests(unittest.TestCase):
    def test_decodes_independent_upstream_vectors(self):
        vectors = (
            ("c0 f4 04 01", 0x100000, ("bg.movhi", 4, (7, 0xA0200000))),
            ("f0 c7 1e ec", 0x100000, ("bg.lbz", 4, (6, 7, 0x1EEC))),
            ("f8 ea 36 d8", 0x100000, ("bg.sb", 4, (7, 10, 0x36D8))),
            ("20 60 44", 0x100000, ("bn.beqi", 3, (3, 0, 0x100011))),
            ("20 c0 5e", 0x100000, ("bn.bnei", 3, (6, 0, 0x100017))),
            ("d1 41 04 aa", 0x100000, ("bg.beqi", 4, (10, 1, 0x100095))),
            ("98 df", 0x100000, ("bt.movi", 2, (6, -1))),
            ("93 f4", 0x100000, ("bt.j", 2, (0x0FFFF4,))),
            ("2f f1 7e", 0x100000, ("bn.j", 3, (0x0FF17E,))),
            ("e7 fe 2f f2", 0x100000, ("bg.jal", 4, (0x0F17F9,))),
        )

        for encoded, pc, expected in vectors:
            with self.subTest(encoded=encoded):
                instruction = decode_instruction(bytes.fromhex(encoded), pc)
                self.assertEqual(
                    (instruction.name, instruction.size, instruction.operands),
                    expected,
                )

    def test_rejects_truncated_and_unsupported_instructions(self):
        with self.assertRaisesRegex(InterpretationError, "truncated"):
            decode_instruction(bytes.fromhex("c0 e0 09"), ENTRY)
        with self.assertRaisesRegex(InterpretationError, "unsupported"):
            decode_instruction(bytes.fromhex("80 02"), ENTRY)


class OriginalBlockTests(unittest.TestCase):
    PRIMARY_BYTES = bytes.fromhex(
        "c0 e0 09 21 f0 e7 30 c4 20 ec 4c d0 e6 06 82 "
        "d0 e9 f5 c6 c0 e0 09 41 f8 c7 fe 1f e7 ff a7 9e 92 a8"
    )
    OUTLINE_BYTES = bytes.fromhex(
        "98 c3 c0 e0 09 41 f8 c7 fe 1f e7 ff a6 0a 2f fd de"
    )

    def test_embedded_blocks_are_the_original_firmware_bytes(self):
        root = Path(__file__).resolve().parents[1]
        default = root / "firmware/m-RG949CCAA-1007.2[2D8D].bin"
        path = Path(os.environ.get("CRG9_FIRMWARE", default))
        if not path.is_file():
            self.skipTest(
                "Set CRG9_FIRMWARE to the original Samsung CCAA 1007.2 BIN"
            )
        image = path.read_bytes()
        self.assertEqual(patcher.sha256(image), patcher.SOURCE_SHA256)
        actual = blocks_from_main(patcher.extract_main(image))
        self.assertEqual(actual, ORIGINAL_BLOCKS)
        self.assertEqual(ORIGINAL_BLOCKS[ENTRY], self.PRIMARY_BYTES)
        self.assertEqual(ORIGINAL_BLOCKS[OUTLINE_ENTRY], self.OUTLINE_BYTES)

    def test_original_special_cases(self):
        previous = 0xA5
        cases = {
            3: (previous, (CALL_TARGET,), ()),
            6: (3, (CALL_TARGET,), ((SELECTOR_ADDRESS, 3),)),
            9: (1, (CALL_TARGET,), ((SELECTOR_ADDRESS, 1),)),
            0x0A: (previous, (), ()),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                result = run(ORIGINAL_BLOCKS, value, previous)
                self.assertEqual(
                    (result.selection, result.calls, result.writes), expected
                )

    def test_original_other_values_return_without_switching(self):
        previous = 0x5A
        for value in set(range(256)) - {3, 6, 9}:
            with self.subTest(value=value):
                result = run(ORIGINAL_BLOCKS, value, previous)
                self.assertEqual(result.selection, previous)
                self.assertEqual(result.calls, ())
                self.assertEqual(result.writes, ())
                self.assertEqual(result.pcs[-1], RETURN_ADDRESS)

    def test_pc_paths_include_only_instruction_boundaries_and_terminal(self):
        expected_paths = {
            3: (ENTRY, ENTRY + 4, ENTRY + 8, ENTRY + 0x1B, CALL_TARGET),
            6: (
                ENTRY,
                ENTRY + 4,
                ENTRY + 8,
                ENTRY + 0x0B,
                OUTLINE_ENTRY,
                OUTLINE_ENTRY + 2,
                OUTLINE_ENTRY + 6,
                OUTLINE_ENTRY + 10,
                CALL_TARGET,
            ),
            9: (
                ENTRY,
                ENTRY + 4,
                ENTRY + 8,
                ENTRY + 0x0B,
                ENTRY + 0x0F,
                ENTRY + 0x13,
                ENTRY + 0x17,
                ENTRY + 0x1B,
                CALL_TARGET,
            ),
            0x0A: (
                ENTRY,
                ENTRY + 4,
                ENTRY + 8,
                ENTRY + 0x0B,
                ENTRY + 0x0F,
                RETURN_ADDRESS,
            ),
        }
        for value, expected in expected_paths.items():
            with self.subTest(value=value):
                self.assertEqual(run(ORIGINAL_BLOCKS, value, 0x44).pcs, expected)

    def test_rejects_branch_to_middle_of_instruction(self):
        # bg.beqi r6,1,+2, followed by a legal return jump.
        word = (0x34 << 26) | (6 << 21) | (1 << 16) | (2 << 3) | 2
        blocks = {
            ENTRY: word.to_bytes(4, "big") + self.PRIMARY_BYTES[4:],
            OUTLINE_ENTRY: self.OUTLINE_BYTES,
        }
        with self.assertRaisesRegex(InterpretationError, "instruction boundary"):
            run(blocks, 0, 0)

    def test_rejects_unexpected_load_store_and_call_targets(self):
        bad_load = dict(ORIGINAL_BLOCKS)
        bad_load[ENTRY] = (
            self.PRIMARY_BYTES[:6] + bytes.fromhex("30 c5") + self.PRIMARY_BYTES[8:]
        )
        with self.assertRaisesRegex(InterpretationError, "read"):
            run(bad_load, 3, 0)

        bad_store = dict(ORIGINAL_BLOCKS)
        bad_store[ENTRY] = (
            self.PRIMARY_BYTES[:25] + bytes.fromhex("fe 20") + self.PRIMARY_BYTES[27:]
        )
        with self.assertRaisesRegex(InterpretationError, "write"):
            run(bad_store, 9, 0)

        bad_call = dict(ORIGINAL_BLOCKS)
        bad_call[ENTRY] = (
            self.PRIMARY_BYTES[:27] + bytes.fromhex("e7 ff a7 9c") + self.PRIMARY_BYTES[31:]
        )
        with self.assertRaisesRegex(InterpretationError, "call target"):
            run(bad_call, 9, 0)

    def test_only_the_two_fixed_block_ranges_are_accepted(self):
        with self.assertRaisesRegex(InterpretationError, "block addresses"):
            run({ENTRY: self.PRIMARY_BYTES}, 3, 0)
        with self.assertRaisesRegex(InterpretationError, "block length"):
            run(
                {ENTRY: self.PRIMARY_BYTES + b"\0", OUTLINE_ENTRY: self.OUTLINE_BYTES},
                3,
                0,
            )


if __name__ == "__main__":
    unittest.main()
