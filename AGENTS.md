# Samsung CRG9 DP2 patcher

- Target model: LC49RG90SSNXZA, original firmware family m-RG949CCAA-1007.2.
- Vendor firmware, derived binaries, and local toolchains are gitignored. Preserve original files under `firmware/` byte-for-byte.
- The distributed patcher must require the exact original-file SHA-256, preserve the input, and write a separate output. The SHA guard establishes file identity; it is not a hardware-safety guarantee.
- The unofficial 1007.3 image installed successfully and switched to DP2 through DDC/CI on one monitor on 2026-09-08. This is not a Samsung release or a general hardware-safety guarantee.
- `README.md` contains usage and installation instructions. The patcher uses only Python's standard library.
- The `full-solution-1007.3` Git tag preserves the investigation, validation records, tests, and worktree setup script removed from the current tree. That tag exists only in the maintainer's local clone and is not published.
- `analysis/PATCH-DESIGN.md` justifies the replacement bytes, `docs/VALIDATION-1007.3.md` records the hardware validation, and `tests/` holds the regression suite. Run `python3 -m unittest discover -s tests` with the original CCAA BIN under `firmware/`; tests needing it skip otherwise. The broader investigation notes remain in the tagged tree only.
