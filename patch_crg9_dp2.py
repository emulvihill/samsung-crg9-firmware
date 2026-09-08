#!/usr/bin/env python3
"""Add DDC input 0x0A (DP2) to one exact Samsung CRG9 firmware image.

Experimental candidate. Version 1007.3 installed successfully on one monitor,
and physical DP2 switching was confirmed. Uses only Python's standard library.
Never flashes a monitor or overwrites a file.
"""
import argparse
import errno
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import zlib


SOURCE_SHA256 = "8fcc3acf15f2988e2ff7ac025a23c7c7b2df69d1a6fa2c2425dc33ad28b38ec8"
ORIGINAL_MAIN_SHA256 = "24f0b4495d240ec5c65ecd3538e5098e56b5959fab19472613c1884d6ba13854"
PATCHED_MAIN_SHA256 = "fafa1d58d8b2317f53ae172ea717d8a7fba911491d6071b632d7a019b03ea69f"
IMAGE_SIZE = 0x300000
MAIN_SIZE = 0x28fc08
MAIN_START = 0x30080
STREAM_START = 0x33880
ORIGINAL_MAIN_END = 0x14d483
FIRST_RESOURCE = 0x14d488
FILE_CRC_OFFSET = 0x2c60b0
LINK_FALLBACK_ERRNOS = frozenset(
    getattr(errno, name) for name in ("EPERM", "EACCES", "ENOSYS", "ENOTSUP", "EOPNOTSUPP")
    if hasattr(errno, name)
)
REPLACEMENTS = (
    (0x69e0b, "d0e9f5c6c0e00941f8c7fe1f", "d0ea0682d0e9f5a690ca9002"),
    (0x69ed7, "98c3c0e00941f8c7fe1fe7ffa60a2ffdde", "98c3900498c2c0e00941f8c7fe1f2fff32"),
)
VERSION_OFFSET = 0x2070ac
ORIGINAL_VERSION = b"m-RG949CCAA-1007.2\0"
CANDIDATE_VERSION = b"m-RG949CCAA-1007.3\0"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=2)
def crc_table(width, polynomial):
    mask = (1 << width) - 1
    table = []
    for byte in range(256):
        value = byte << (width - 8)
        for _ in range(8):
            top = value & (1 << (width - 1))
            value = ((value << 1) ^ (polynomial if top else 0)) & mask
        table.append(value)
    return tuple(table)


def crc(data, width, polynomial):
    """MSB first, initial value zero, final XOR zero (MStar packer convention)."""
    table = crc_table(width, polynomial)
    mask = (1 << width) - 1
    value = 0
    for byte in data:
        value = ((value << 8) & mask) ^ table[(value >> (width - 8)) ^ byte]
    return value


def extract_main(image):
    end = struct.unpack_from("<I", image, 0x1000c)[0]
    decoder = zlib.decompressobj(-15)
    main = decoder.decompress(image[STREAM_START:end])
    require(decoder.eof and len(decoder.unused_data) == 6, "Unexpected DEFLATE trailer")
    require(len(main) == MAIN_SIZE, "Unexpected decompressed size")
    require(struct.unpack_from("<I", decoder.unused_data)[0] == len(main), "Size trailer mismatch")
    return main


def patch_bytes(original):
    observed = sha256(original)
    require(len(original) == IMAGE_SIZE and observed == SOURCE_SHA256,
            f"Wrong firmware: expected {IMAGE_SIZE} bytes and SHA-256 {SOURCE_SHA256}; "
            f"received {len(original)} bytes and SHA-256 {observed}")
    main = bytearray(extract_main(original))
    require(sha256(main) == ORIGINAL_MAIN_SHA256, "Original application SHA-256 mismatch")
    for offset, before_hex, after_hex in REPLACEMENTS:
        before, after = bytes.fromhex(before_hex), bytes.fromhex(after_hex)
        require(len(before) == len(after) and main[offset:offset + len(before)] == before,
                f"Unexpected instruction bytes at 0x{offset:x}")
        main[offset:offset + len(before)] = after
    require(len(ORIGINAL_VERSION) == len(CANDIDATE_VERSION) and
            main[VERSION_OFFSET:VERSION_OFFSET + len(ORIGINAL_VERSION)] == ORIGINAL_VERSION,
            f"Unexpected version string at 0x{VERSION_OFFSET:x}")
    main[VERSION_OFFSET:VERSION_OFFSET + len(ORIGINAL_VERSION)] = CANDIDATE_VERSION
    require(sha256(main) == PATCHED_MAIN_SHA256, "Patched application SHA-256 mismatch")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    compressed = compressor.compress(main) + compressor.flush()
    require(zlib.decompress(compressed, -15) == main, "Recompression round-trip failed")
    main_end = STREAM_START + len(compressed) + 6
    require(main_end <= ORIGINAL_MAIN_END, "Compressed result does not fit the original allocation")
    result = bytearray(original)
    result[STREAM_START:FIRST_RESOURCE] = (
        compressed + struct.pack("<I", len(main)) + bytes(2) + bytes(FIRST_RESOURCE - main_end)
    )
    main_length = main_end - MAIN_START
    struct.pack_into("<I", result, 0x10008, 0x200000 + main_length)
    struct.pack_into("<I", result, 0x1000c, main_end)
    struct.pack_into("<I", result, 0x311ca, main_length)
    # The vendor packer calculates this CRC16 BEFORE writing the AP CRC32.
    result[0x10014:0x10018] = bytes(4)
    struct.pack_into(">H", result, main_end - 2, crc(result[0x10000:main_end - 2], 16, 0x8005))
    struct.pack_into("<I", result, 0x10014, crc(result[MAIN_START:main_end], 32, 0x04c11db7))
    struct.pack_into(">I", result, FILE_CRC_OFFSET, crc(result[:FILE_CRC_OFFSET], 32, 0x04c11db7))
    require(len(result) == IMAGE_SIZE, "Output image size changed")
    require(result[FIRST_RESOURCE:FILE_CRC_OFFSET] == original[FIRST_RESOURCE:FILE_CRC_OFFSET],
            "Resource data changed")
    require(sha256(extract_main(result)) == PATCHED_MAIN_SHA256, "Stored application verification failed")
    return bytes(result)


def samsung_filename(image):
    checksum = sum(image[:FILE_CRC_OFFSET + 4]) & 0xffff
    return f"m-RG949CCAA-1007.3[{checksum:04X}].bin"


def write_new_file(path, data):
    """Refuse existing paths; use atomic publication when hard links are available."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        require(sha256(temporary.read_bytes()) == sha256(data), "Written file SHA-256 mismatch")
        # A hard link creates the destination without the overwrite behavior of rename/replace.
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise
        except OSError as error:
            # Win32 may report unsupported hard links with EINVAL plus a
            # specific winerror (INVALID_FUNCTION=1 or NOT_SUPPORTED=50).
            if error.errno not in LINK_FALLBACK_ERRNOS and getattr(error, "winerror", None) not in {1, 50}:
                raise
            # FAT and some network filesystems lack hard links. Exclusive creation
            # still prevents overwrites; remove our own output if writing fails.
            created = False
            try:
                with path.open("xb") as stream:
                    created = True
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                require(sha256(path.read_bytes()) == sha256(data), "Written file SHA-256 mismatch")
            except BaseException:
                if created:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                raise
    finally:
        temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="exact original CCAA 1007.2 BIN")
    parser.add_argument("output", type=Path, nargs="?", help="new output path (default: checksum filename beside input)")
    args = parser.parse_args(argv)
    try:
        if args.output is not None and os.path.lexists(args.output):
            raise ValueError(f"Output already exists; refusing to overwrite: {args.output}")
        patched = patch_bytes(args.input.read_bytes())
        name = samsung_filename(patched)
        output = args.output if args.output is not None else args.input.with_name(name)
        write_new_file(output, patched)
        print(f"Original SHA-256 verified: {SOURCE_SHA256}")
        print(f"Wrote: {output}")
        print(f"Samsung filename: {name}")
        print(f"Output SHA-256: {sha256(patched)}")
        print(f"Patched application SHA-256: {PATCHED_MAIN_SHA256}")
        print(f"zlib: {zlib.ZLIB_RUNTIME_VERSION}")
        print("Status: installed and DP2 switching confirmed on one monitor.")
        return 0
    except (OSError, ValueError, zlib.error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
