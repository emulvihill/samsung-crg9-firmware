from dataclasses import dataclass
from typing import Mapping


ENTRY = 0x26D5FC
OUTLINE_ENTRY = 0x26D6D7
RETURN_ADDRESS = 0x26D4C3
CALL_TARGET = 0x26A9E6
INPUT_ADDRESS = 0x4930C4
SELECTOR_ADDRESS = 0x49FE1F

ORIGINAL_BLOCKS = {
    ENTRY: bytes.fromhex(
        "c0 e0 09 21 f0 e7 30 c4 20 ec 4c d0 e6 06 82 "
        "d0 e9 f5 c6 c0 e0 09 41 f8 c7 fe 1f e7 ff a7 9e 92 a8"
    ),
    OUTLINE_ENTRY: bytes.fromhex(
        "98 c3 c0 e0 09 41 f8 c7 fe 1f e7 ff a6 0a 2f fd de"
    ),
}

_BLOCK_LENGTHS = {ENTRY: 0x21, OUTLINE_ENTRY: 0x11}
RUNTIME_BASE = 0x203800


def blocks_from_main(main: bytes) -> dict[int, bytes]:
    blocks = {}
    for address, length in _BLOCK_LENGTHS.items():
        offset = address - RUNTIME_BASE
        end = offset + length
        if offset < 0 or end > len(main):
            raise InterpretationError(
                f"application does not contain block at 0x{address:06X}"
            )
        blocks[address] = bytes(main[offset:end])
    return blocks


@dataclass(frozen=True)
class Instruction:
    name: str
    size: int
    operands: tuple[int, ...]


@dataclass(frozen=True)
class Result:
    selection: int
    calls: tuple[int, ...]
    writes: tuple[tuple[int, int], ...]
    pcs: tuple[int, ...]


class InterpretationError(ValueError):
    pass


def _signed(value: int, width: int) -> int:
    sign = 1 << (width - 1)
    return (value ^ sign) - sign


def _word(data: bytes, size: int, pc: int) -> int:
    if len(data) < size:
        raise InterpretationError(f"truncated instruction at 0x{pc:06X}")
    return int.from_bytes(data[:size], "big")


def decode_instruction(data: bytes, pc: int) -> Instruction:
    word16 = _word(data, 2, pc)
    prefix = word16 >> 13

    if prefix == 4:
        if word16 & 0xFC00 == 0x9000:
            target = pc + _signed(word16 & 0x3FF, 10)
            return Instruction("bt.j", 2, (target,))
        if word16 & 0xFC00 == 0x9800:
            rd = (word16 >> 5) & 0x1F
            immediate = _signed(word16 & 0x1F, 5)
            return Instruction("bt.movi", 2, (rd, immediate))

    if prefix < 4:
        word24 = _word(data, 3, pc)
        opcode = word24 & 0xFC0000
        if opcode == 0x200000:
            condition = word24 & 0x3
            names = {0: "bn.beqi", 2: "bn.bnei"}
            if condition in names:
                ra = (word24 >> 13) & 0x1F
                immediate = (word24 >> 10) & 0x7
                displacement = _signed((word24 >> 2) & 0xFF, 8)
                return Instruction(
                    names[condition], 3, (ra, immediate, pc + displacement)
                )
        if opcode == 0x2C0000:
            displacement = _signed(word24 & 0x3FFFF, 18)
            return Instruction("bn.j", 3, (pc + displacement,))

    if prefix > 4:
        word32 = _word(data, 4, pc)
        opcode = word32 >> 26
        if opcode == 0x30 and word32 & 1:
            rd = (word32 >> 21) & 0x1F
            immediate = ((word32 >> 5) & 0xFFFF) << 16
            return Instruction("bg.movhi", 4, (rd, immediate))
        if opcode == 0x34:
            condition = word32 & 0x7
            names = {2: "bg.beqi", 6: "bg.bnei"}
            if condition in names:
                ra = (word32 >> 21) & 0x1F
                immediate = (word32 >> 16) & 0x1F
                displacement = _signed((word32 >> 3) & 0x1FFF, 13)
                return Instruction(
                    names[condition], 4, (ra, immediate, pc + displacement)
                )
        if opcode == 0x39 and not word32 & 1:
            displacement = _signed((word32 >> 1) & 0x1FFFFFF, 25)
            return Instruction("bg.jal", 4, (pc + displacement,))
        if opcode == 0x3C:
            rd = (word32 >> 21) & 0x1F
            base = (word32 >> 16) & 0x1F
            offset = _signed(word32 & 0xFFFF, 16)
            return Instruction("bg.lbz", 4, (rd, base, offset))
        if opcode == 0x3E:
            source = (word32 >> 21) & 0x1F
            base = (word32 >> 16) & 0x1F
            offset = _signed(word32 & 0xFFFF, 16)
            return Instruction("bg.sb", 4, (source, base, offset))

    raise InterpretationError(f"unsupported instruction at 0x{pc:06X}")


def run(blocks: Mapping[int, bytes], value: int, previous_selector: int) -> Result:
    if set(blocks) != set(_BLOCK_LENGTHS):
        raise InterpretationError("unexpected block addresses")
    if not 0 <= value <= 0xFF or not 0 <= previous_selector <= 0xFF:
        raise InterpretationError("input and selector values must be bytes")

    decoded: dict[int, Instruction] = {}
    for start, expected_length in _BLOCK_LENGTHS.items():
        data = bytes(blocks[start])
        if len(data) != expected_length:
            raise InterpretationError(f"unexpected block length at 0x{start:06X}")
        offset = 0
        while offset < len(data):
            pc = start + offset
            instruction = decode_instruction(data[offset:], pc)
            decoded[pc] = instruction
            offset += instruction.size
        if offset != len(data):
            raise InterpretationError(f"instruction crosses block end at 0x{pc:06X}")

    terminals = {CALL_TARGET, RETURN_ADDRESS}
    for pc, instruction in decoded.items():
        if instruction.name in {"bn.beqi", "bn.bnei", "bg.beqi", "bg.bnei"}:
            target = instruction.operands[2]
            if target not in decoded and target not in terminals:
                raise InterpretationError(
                    f"branch target 0x{target:06X} is not an instruction boundary"
                )
        elif instruction.name in {"bt.j", "bn.j"}:
            target = instruction.operands[0]
            if target not in decoded and target != RETURN_ADDRESS:
                raise InterpretationError(
                    f"jump target 0x{target:06X} is not an instruction boundary"
                )
        elif instruction.name == "bg.jal" and instruction.operands[0] != CALL_TARGET:
            raise InterpretationError(
                f"unexpected call target 0x{instruction.operands[0]:06X}"
            )

    registers = [0] * 32
    registers[6] = 1
    memory = {INPUT_ADDRESS: value, SELECTOR_ADDRESS: previous_selector}
    calls: list[int] = []
    writes: list[tuple[int, int]] = []
    pcs: list[int] = []
    visited: set[int] = set()
    pc = ENTRY

    while True:
        if pc == RETURN_ADDRESS:
            pcs.append(pc)
            break
        if pc in visited:
            raise InterpretationError(f"control-flow cycle at 0x{pc:06X}")
        visited.add(pc)
        instruction = decoded.get(pc)
        if instruction is None:
            raise InterpretationError(
                f"PC 0x{pc:06X} is not an instruction boundary"
            )
        pcs.append(pc)
        next_pc = pc + instruction.size
        name = instruction.name
        operands = instruction.operands

        if name == "bg.movhi":
            rd, immediate = operands
            registers[rd] = immediate
        elif name == "bg.lbz":
            rd, base, offset = operands
            address = (registers[base] + offset) & 0xFFFFFFFF
            if address != INPUT_ADDRESS:
                raise InterpretationError(f"unexpected memory read at 0x{address:08X}")
            registers[rd] = memory[address]
        elif name == "bg.sb":
            source, base, offset = operands
            address = (registers[base] + offset) & 0xFFFFFFFF
            if address != SELECTOR_ADDRESS:
                raise InterpretationError(f"unexpected memory write at 0x{address:08X}")
            stored = registers[source] & 0xFF
            memory[address] = stored
            writes.append((address, stored))
        elif name == "bt.movi":
            rd, immediate = operands
            registers[rd] = immediate
        elif name in {"bn.beqi", "bg.beqi"}:
            ra, immediate, target = operands
            if registers[ra] == immediate:
                next_pc = target
        elif name in {"bn.bnei", "bg.bnei"}:
            ra, immediate, target = operands
            if registers[ra] != immediate:
                next_pc = target
        elif name in {"bt.j", "bn.j"}:
            next_pc = operands[0]
        elif name == "bg.jal":
            calls.append(CALL_TARGET)
            pcs.append(CALL_TARGET)
            break
        else:
            raise InterpretationError(f"unsupported operation {name} at 0x{pc:06X}")
        pc = next_pc

    return Result(
        memory[SELECTOR_ADDRESS], tuple(calls), tuple(writes), tuple(pcs)
    )
