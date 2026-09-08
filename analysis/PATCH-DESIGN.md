# CCAA 1007.2 DP2 patch design

Date: 2026-09-08

Status: implemented candidate. Version 1007.3 installed successfully and DDC/CI
switching to DP2 was confirmed on one monitor. Validation is recorded in
`../docs/VALIDATION-1007.3.md`. The specified safeguard is exact input-file
SHA-256 validation.

## Scope

Build a small, shareable Python standard-library patcher for the exact official Samsung image `m-RG949CCAA-1007.2[2D8D].bin`:

- input size: 3,145,728 bytes
- input SHA-256: `8fcc3acf15f2988e2ff7ac025a23c7c7b2df69d1a6fa2c2425dc33ad28b38ec8`
- model/family: LC49RG90SSNXZA, CCAA 1007.2

The patcher must refuse every other input, preserve the input file, write a
separate output, increment the embedded candidate version to 1007.3, and verify
the fixed final decompressed-main SHA-256
`fafa1d58d8b2317f53ae172ea717d8a7fba911491d6071b632d7a019b03ea69f`.
It must not download firmware, package the vendor image, invoke DDC, or flash
the monitor. The final container SHA-256 is printed rather than fixed because
standard-library zlib output can vary across runtime versions.

## Recommended code change

The setter already maps VCP input `0x09` to selector 1 (DP1) and `0x06` to selector 3 (HDMI). The getter reports selector 2 as `0x0a`, but the setter rejects it. Preserve the incomplete `0x03` case and all other rejection behavior. Add only `0x0a -> selector 2`, then call the same selection routine used by DP1 and HDMI.

Two same-length decompressed-code replacements provide enough space. Runtime addresses map to decompressed-stream offsets by subtracting `0x203800`.

### Block A: dispatch

Runtime range `0x26d60b..0x26d617`, stream range `0x69e0b..0x69e17`, 12 bytes:

```text
old: d0 e9 f5 c6  c0 e0 09 41  f8 c7 fe 1f
new: d0 ea 06 82  d0 e9 f5 a6  90 ca 90 02
```

Candidate disassembly and intent:

```text
26d60b  d0ea0682  bg.beqi r7,0x0a,26d6db   ; new DP2 case
26d60f  d0e9f5a6  bg.bnei r7,0x09,26d4c3   ; relocated original rejection test
26d613  90ca      bt.j 26d6dd              ; DP1 uses shared store
26d615  9002      bt.j 26d617              ; valid unreachable filler
26d617  e7ffa79e  bg.jal 26a9e6            ; unchanged shared call
```

`bg.bnei` is the semantic name supported by the surrounding switch control flow and the independent review. Reko currently prints this opcode as the guessed mnemonic `bg.bltui?`. The candidate deliberately preserves the original opcode/sub-opcode and immediate `0x09`; only its PC-relative displacement changes when it moves four bytes.

### Block B: selector stubs and shared store

Runtime range `0x26d6d7..0x26d6e8`, stream range `0x69ed7..0x69ee8`, 17 bytes:

```text
old: 98 c3 c0 e0 09 41 f8 c7 fe 1f e7 ff a6 0a 2f fd de
new: 98 c3 90 04 98 c2 c0 e0 09 41 f8 c7 fe 1f 2f ff 32
```

Candidate disassembly and intent:

```text
26d6d7  98c3      bt.movi r6,3             ; existing HDMI selector
26d6d9  9004      bt.j 26d6dd              ; HDMI joins shared store
26d6db  98c2      bt.movi r6,2             ; new DP2 selector stub
26d6dd  c0e00941  bg.movhi r7,0x49fe1f@hi  ; copied existing store
26d6e1  f8c7fe1f  bg.sb 0x49fe1f@lo(r7),r6
26d6e5  2fff32    bn.j 26d617               ; reuse existing selection call
26d6e8  e7ffd876  bg.jal 26c323             ; unchanged next block
```

The three-byte `bn.j` is preferred over a two-byte jump plus a stray byte: it fills the original 17-byte region exactly and leaves `0x26d6e8` aligned at its original address.

## Encoding and range proof

Aeon branches use byte displacements relative to the instruction address in the pinned Reko decoder.

| Instruction | Encoding derivation | Displacement | Encodable range | Result |
| --- | --- | ---: | ---: | --- |
| `bg.beqi r7,0x0a,0x26d6db` at `0x26d60b` | opcode `110100`, r7, imm5 `0x0a`, signed 13-bit displacement in bits 3..15, sub-opcode `010` | `+0xd0` | -4096..4095 | `d0ea0682` |
| relocated original reject test at `0x26d60f` | original opcode/sub-opcode `110`, r7 and imm5 `0x09`; replace displacement only | `-0x14c` | -4096..4095 | `d0e9f5a6` |
| `bt.j 0x26d6dd` at `0x26d613` | opcode `100100`, signed 10-bit displacement | `+0xca` | -512..511 | `90ca` |
| `bt.j 0x26d617` at `0x26d615` | same form | `+2` | -512..511 | `9002` |
| `bt.j 0x26d6dd` at `0x26d6d9` | same form | `+4` | -512..511 | `9004` |
| `bn.j 0x26d617` at `0x26d6e5` | opcode `001011`, signed 18-bit displacement | `-0xce` | -131072..131071 | `2fff32` |

`bt.movi r6,2` is `98c2`, matching the existing `bt.movi r6,3` encoding `98c3`. The MOVHI and byte-store instructions are copied unchanged from the original HDMI block.

Static direct-target screening of the current full listing finds entries into these regions only at `0x26d6d7` (the existing VCP `0x06` branch) and `0x26d6e8` (an unrelated existing branch). Both addresses and their instructions remain valid. The existing `0x03` branch to `0x26d617` also remains valid. No decoded direct branch targets the overwritten interiors. This does not rule out an undiscovered indirect transfer, so the patched full-stream control-flow scan remains a required check.

## Path proof

| VCP low byte | Candidate path | Selector/action |
| --- | --- | --- |
| `0x03` | existing branch at `0x26d604` directly to `0x26d617` | preserve original incomplete/stale behavior exactly |
| `0x06` | existing branch to `0x26d6d7`, set r6=3, join shared store | selector 3, then existing selection call |
| `0x09` | new branch-to-reject test falls through, jump to shared store | selector 1 already held in r6, then existing selection call |
| `0x0a` | new equality branch to `0x26d6db`, set r6=2 | selector 2, then existing selection call |
| every other value | relocated original not-equal-`0x09` test branches to `0x26d4c3` | reject as before |

The getter and menu trace independently associate selector 2 with source index 1, the DP2 position. The binary rewrite is still a candidate until decoded from the patched bytes and exercised on hardware.

## Compressed-image strategy

The two code blocks are inside the raw-DEFLATE main stream. Rebuild that stream at runtime with Python's standard-library `zlib.compressobj(level=9, wbits=-15)`. This keeps the distributed patcher small and avoids embedding any vendor firmware data beyond the 29 original/replacement bytes above.

The patcher must:

1. Extract and inflate the original stream from fixed file offset `0x33880`; require raw-DEFLATE EOF, decompressed size 2,685,960, and original decompressed SHA-256 `24f0b4495d240ec5c65ecd3538e5098e56b5959fab19472613c1884d6ba13854`.
2. Require the exact original bytes at stream offsets `0x69e0b` and `0x69ed7`,
   apply the two same-length replacements and the 1007.3 version-byte change,
   then require patched decompressed SHA-256
   `fafa1d58d8b2317f53ae172ea717d8a7fba911491d6071b632d7a019b03ea69f`.
3. Compress the patched stream as raw DEFLATE at level 9 and immediately inflate it again; require an exact byte match with the patched decompressed stream.
4. Require the rebuilt main image, four-byte size trailer, and two-byte CRC16 to end no later than the original main endpoint `0x14d483`. This deliberately retains the original allocation bound, including the five-byte gap before the first resource at `0x14d488`. Reject an oversized result rather than relocating directory resources. Python zlib 1.3.1 produced 1,153,055 compressed bytes, 990 fewer than the original 1,154,045 bytes.
5. Write the rebuilt main image at `0x33880`, then zero the gap through `0x14d488`. Preserve all directory entries, resource contents and the overall 3,145,728-byte file size.

Compressor output may vary across zlib versions. That is acceptable: the exact source-file hash and patched decompressed hash fix the semantic payload, and all length and integrity fields are derived from the actual rebuilt stream. Print the zlib runtime version and final output SHA-256 so each generated file is identifiable.

## Integrity fields

After the exact stored DEFLATE bytes are finalized, update fields in this order:

1. Let `main_length = 0x3800 + raw_deflate_length + 4 + 2`. Write `0x200000 + main_length` little-endian at `0x10008`, `0x30080 + main_length` little-endian at `0x1000c`, and `main_length` little-endian at `0x311ca`. The original value is `0x11d403`.
2. Put the unchanged decompressed size `0x0028fc08` little-endian immediately after raw-DEFLATE EOF. Reserve the next two bytes for the main CRC16. Zero any remaining bytes through `0x14d488`, the first resource offset.
3. Zero bytes `0x10014..0x10018`. Compute non-reflected CRC16 (polynomial `0x8005`, init 0, xorout 0) over `[0x10000, main_end-2)`, and write it big-endian at `main_end-2`. This exactly reproduces original CCAA value `0xbfe3` and PA value `0x6834`. Public MStar packer code explains the ordering: it writes this CRC16 while the application CRC32 field is still zero.
4. Compute non-reflected CRC32 (polynomial `0x04c11db7`, init 0, xorout 0) over `[0x30080, main_end)`, including the new CRC16, and write it little-endian at `0x10014`.
5. Preserve the used-image endpoint `LE32(0x10020) = 0x2c60b0` and every directory resource offset. Recompute the same CRC32 over `[0, 0x2c60b0)`, and write it big-endian at `0x2c60b0`.
6. Recompute the filename checksum as `sum(output[0:0x2c60b4]) & 0xffff`; use the resulting uppercase four hex digits in the default output filename.

The main CRC16 rule was independently reproduced on both firmware families and is recorded in `analysis/crc16-main-ordering.json`; its public-source precedent is `analysis/references/mstar-BinIDPackFiles_Compress.py`. Re-run all structural checks rather than assuming there are no further integrity fields. Signature enforcement remains unknown.

## Patcher interface

Use one dependency-free script with this behavior:

```text
python3 patch_crg9_dp2.py INPUT.bin [OUTPUT.bin]
```

- Read and hash the complete input before creating an output.
- Require the exact size and SHA-256 above; print both observed values on refusal.
- If an output path is omitted, derive `m-RG949CCAA-1007.3[CHECKSUM].bin`
  after computing the updated filename checksum. Samsung's manual requires this
  naming format; do not add a `-dp2` suffix or change the product family.
- Refuse an output path equal to the input path and refuse to overwrite an existing file.
- Inflate the main stream, require its original size and SHA-256, and require the expected original bytes before applying each decompressed-code replacement.
- Recompress the fixed patched payload, verify it by inflating it again, recompute the integrity fields, then require the fixed 3,145,728-byte output size. Print the resulting output SHA-256.
- Write and verify a temporary sibling file, flush and `fsync`, then publish by hard link without replacing an existing path. If the filesystem does not support hard links, use exclusive creation, write/flush/`fsync`, and verify the destination hash. This fallback supports FAT; publication there is not atomic against abrupt termination.
- On a reported write failure, remove only the patcher's own temporary file and newly created incomplete output; leave the input and any preexisting output untouched.
- Print the original and generated hashes, zlib runtime, and a brief status:
  candidate installed and DP2 switching confirmed on one monitor. The safeguard
  validates file identity; it does not guarantee hardware behavior.

Do not add firmware discovery, downloads, ZIP handling, model guessing, USB copying, DDC commands, or flashing. Those features increase the chance of selecting the wrong artifact and are outside the patcher's narrow scope.

## Verification gates before release

1. Assemble or independently encode both candidate regions, disassemble them from their true runtime addresses with the pinned Reko build, and compare every instruction and target with this design.
2. Confirm that patched control flow has the five paths in the table above and that a whole-stream scan introduces no direct target into an instruction interior.
3. Inflate the final stored stream and byte-compare it with the expected patched decompressed stream. Permit differences only in the two declared ranges.
4. Confirm the raw-DEFLATE EOF and stored length agree with all three length/end fields, the decompressed size and fixed patched decompressed SHA-256 match, the main CRC16 and both CRC32 fields reproduce, all directory bounds/resource checks remain valid, the used-image endpoint is unchanged, and the filename checksum and output size match. Record the generated output SHA-256 and zlib version.
5. Test the patcher with Python's standard-library test framework: exact input succeeds; one-byte-corrupt input, wrong-family PA image, truncated input, pre-existing output, and same input/output path all refuse without changing the source.
6. Run the patcher twice from independent pristine copies under the same Python/zlib runtime and require byte-identical outputs. Across different zlib versions, permit different container bytes only when every structural check passes and the patched decompressed SHA-256 is identical.
7. Under a controlled flashing and recovery procedure, test GET/SET VCP `0x60` for DP1 `0x09`, DP2 `0x0a`, and HDMI `0x06`; confirm the physical menu still selects all three. Also verify that representative unsupported values remain rejected and record what `0x03` does.

Only after the hardware test succeeds should this be described as a patch for other owners. Distribution should include the patcher, its source and output hashes, the exact supported model/version, instructions for obtaining Samsung's official image, and the observed test results. It should not include the original or patched full firmware image.
