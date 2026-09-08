# Samsung CRG9 DP2 patcher

Experimental patcher for Samsung's **m-RG949CCAA-1007.2**. 

The CRG9 (CCAA) monitor which I purchased lacks the ability to switch to DisplayPort 2 via DDC, although HDMI and DP1 work fine. This is due to a missing branch in the switching function's mappings from port id -> output.

It produces a local
modified firmware file identified as **1007.3**, and adds DDC/CI input value
`0x0A` for DisplayPort 2, preserving `0x09` for DP1, `0x06` for HDMI, and the
existing behavior of other values. The 1007.3 candidate has installed
successfully on one LC49RG90SSNXZA monitor, and DDC/CI switching to DP2 was
confirmed. The 1007.3 label identifies this modified candidate; it is not a
Samsung release.

The patcher checks the complete original file's SHA-256 before changing anything.
It uses Python's standard library, writes a separate file, and refuses to
overwrite an existing file. It neither downloads nor flashes firmware.

## Required original

```text
m-RG949CCAA-1007.2[2D8D].bin
3145728 bytes
SHA-256: 8fcc3acf15f2988e2ff7ac025a23c7c7b2df69d1a6fa2c2425dc33ad28b38ec8
```

Download Samsung's archive and extract that BIN. The CCPA image in the same
archive is a different input and will be refused.

```text
https://org.downloadcenter.samsung.com/downloadfile/ContentsFile.aspx?CDSite=US&CttFileID=7978758&CDCttType=FM&ModelType=C&ModelName=LC49RG90SSNXZA&VPath=FM/202302/20230209154454647/CRG9_firmware.zip
```

## Run

```sh
python3 patch_crg9_dp2.py '/path/to/m-RG949CCAA-1007.2[2D8D].bin'
```

The new BIN appears beside the input with its recalculated checksum in the
filename. An optional second argument selects a new output path. Keep the
printed **Samsung filename** if preparing a firmware USB drive: Samsung's
documented naming rule does not include an extra `-dp2` suffix.

The script prints the output SHA-256 and labels it as a candidate. With the
runtime used for this investigation, the output is:

```text
m-RG949CCAA-1007.3[0DF6].bin
SHA-256: ceee9546f260472af1fc73a086902ed8711ac65c9dcf3806aa639e6c00109472
```

Compressed bytes can differ between zlib runtimes. Every successful result must
inflate to this exact patched application SHA-256:

```text
fafa1d58d8b2317f53ae172ea717d8a7fba911491d6071b632d7a019b03ea69f
```

The tool refuses a compressed result larger than the original allocation.
Publication is atomic on filesystems with hard links; FAT and similar filesystems
use exclusive file creation and remove the new file if a write reports failure.

## Change and verification

The DP2 fix changes 25 application bytes within two replacement ranges totaling
29 bytes. One additional byte changes the embedded version from 1007.2 to
1007.3, matching the filename. The script recompresses the application and
updates the affected length fields, main CRC16, application CRC32, file CRC32,
and filename checksum. All
resource positions and contents remain unchanged.

## Install on a USB drive

Copy only the generated Samsung-named BIN to the root of an MBR/FAT32 USB drive.
Connect it to the monitor's USB port 1, then follow Samsung's firmware-update
procedure for the CRG9. Do not interrupt power while the update is running.
This repository does not automate installation.

```text
https://org.downloadcenter.samsung.com/downloadfile/ContentsFile.aspx?CDSite=UNI_BD&OriginYN=N&ModelType=N&ModelName=C49RG90SSW&CttFileID=8287226&CDCttType=UM&VPath=UM%2F202108%2F20210824162657906%2FBN46-00901B-Eng.pdf
```

This experimental patch is provided without warranty; see `LICENSE`. Running
the script or flashing modified firmware may damage your monitor. The original
and modified full firmware images are not distributed in this repository.

## License and trademarks

The patcher and repository documentation are available under the MIT License;
see `LICENSE`. Samsung and DisplayPort are trademarks of their respective
owners. This project is independent and is not affiliated with or endorsed by
Samsung.
