# Local candidate 1007.3 validation

Date: 2026-09-08. This is a locally modified candidate, not a Samsung release.
Firmware installation and DDC/CI switching to DP2 succeeded on one
LC49RG90SSNXZA monitor.

## Reason for the revision

Neither the original nor the 1007.2 candidate was found on the original
GPT/FAT32 test drive. After rebuilding that drive as MBR/FAT32, the monitor
recognized the candidate but displayed:

> The software version of the device is later than the software version of the update file.

The revised patcher increments the minor version in the embedded identification
string and output filename to 1007.3.
It still requires Samsung's exact original 1007.2 input SHA-256:

```text
8fcc3acf15f2988e2ff7ac025a23c7c7b2df69d1a6fa2c2425dc33ad28b38ec8
```

## Version check traced in the installed firmware

The original application enumerator at runtime 0x2ad3e8 checks the `.bin`
extension and first 11 filename characters against the product prefix. It
parses filename character positions 12, 13, 14, 15, and 17 into a numeric
version at 0x2ad54b–0x2ad581, and the corresponding characters of its own
reference string at 0x2ad586–0x2ad5a2. Thus 1007.2 becomes 10072 and 1007.3
becomes 10073.

At 0x2ad5a4, a strict unsigned greater-than branch accepts at 0x2ad650.
An equal version falls through and sets flag 0x4a18b4 at 0x2ad5a8–0x2ad5ae.
The caller at 0x267cc9–0x267cf7 selects version-error UI state 2 for that flag,
versus no-file state 3 otherwise. This explains the observed equal-version
rejection. The displayed message is present in language resource 0x0826 as
UTF-16LE text near decompressed resource offset 0x1628a.

Reko labels the register comparison with a guessed greater-or-equal mnemonic.
The instruction's condition subcode 5 and the sibling decoder's inverse-pair
layout support strict greater-than instead, consistent with the observed result.
The independent reviewer traced the comparison; uncorrected Reko RTL was not
used as an execution oracle.

This check runs in the currently installed 1007.2 firmware. A filename tagged
1007.3 is greater than that running reference. The candidate's embedded 1007.3
string will identify the installed result and provide its future upgrade
baseline; it does not change the running reference before installation.
Passing this particular version condition does not establish acceptance by all
remaining updater checks.

## Exact additional application change

Relative to the previously validated DP2 candidate, only decompressed byte
0x2070bd changes, from ASCII `2` to `3`. It is the final digit in the complete
null-terminated string at offset 0x2070ac. The two DP2 instruction replacements
are unchanged. Relative to Samsung's original, 26 application bytes differ:
25 instruction bytes and this one version digit.

Independently applying that single digit change to the prior candidate gives
this expected revised application SHA-256:

```text
fafa1d58d8b2317f53ae172ea717d8a7fba911491d6071b632d7a019b03ea69f
```

The criteria were recorded before the revised build in
`analysis/VERSION-1007-3-VALIDATION.md`. The instruction design and checksum
reconstruction remain documented in `analysis/PATCH-DESIGN.md`; the preceding
1007.2 artifact hashes are historical.

## Generated artifact and local checks

The CLI was run on the pristine original and wrote:

```text
analysis/candidate-1007.3/m-RG949CCAA-1007.3[0DF6].bin
SHA-256: ceee9546f260472af1fc73a086902ed8711ac65c9dcf3806aa639e6c00109472
```

The generated application matches the independent expected SHA above. The
complete image remains 3,145,728 bytes. With zlib 1.3.1, the compressed stream
is 1,153,055 bytes and the main endpoint remains 0x14d0a5, fitting the original
allocation by 990 bytes. Main CRC16 is CA58, application CRC32 is 8D8BBCE9,
file CRC32 is 43EB2F34, and the filename checksum is 0DF6. The local build
record is `analysis/candidate-1007.3/build-record.json`.

The complete real-fixture suite passed **28 tests with zero skips**. It checks
the exact extra version-byte change, fixed application SHA, resource/content
preservation, independent bit-at-a-time CRC calculations, length fields, exact
source identity, wrong-input rejection, and output-file safeguards. The actual
revised image's handler passed all 65,536 input/state combinations in 3.258964
seconds after a 0.000034-second timed unit, with zero resumed groups. The new
version regression failed against the preceding implementation before the fix.

No monitor installation was performed during the offline validation above.
Subsequent hardware testing accepted and installed the 1007.3 candidate. A
`setvcp 60 0x0A` command then switched the monitor to DP2 successfully.

## USB preparation

After independent review reproduced the entire candidate byte-for-byte and
recomputed all integrity fields, the revised image replaced the 1007.2
candidate at the root of the MBR/FAT32 test drive:

```text
m-RG949CCAA-1007.3[0DF6].bin
```

The USB was synced and read back with SHA-256 matching the artifact above. It
contains only this firmware BIN; the preceding candidate is preserved locally.
The copy record is `analysis/candidate-1007.3/usb-copy.json`. The monitor later
accepted and installed this revised candidate.
