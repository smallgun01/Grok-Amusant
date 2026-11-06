# Third-party notices

This file identifies third-party material and build dependencies relevant to this candidate. It does not grant a license to Amusant-owned code; the project-wide license is pending the owner's decision.

## Kai

The consolidated firmware patch modifies Kai v0.12.0 at commit `1681fb3c11acd28525d8987bba4b9bf4bc2a288f`; the relay Opus implementation is also adapted from Kai. Kai is distributed under the MIT license. Preserve [third_party/Kai-LICENSE.txt](third_party/Kai-LICENSE.txt) with the applicable source and distributions. Source: https://github.com/zbruceli/kai/tree/1681fb3c11acd28525d8987bba4b9bf4bc2a288f

## Nayuki QR Code generator

The consolidated patch contains Project Nayuki's C QR Code generator and its MIT copyright and permission notice in the added `qrcodegen.c` and `qrcodegen.h` content. Preserve those notices when extracting, modifying, or distributing that code. Source: https://www.nayuki.io/page/qr-code-generator-library

## M5GFX, LovyanGFX and efont

The firmware dependency configuration pins M5GFX 0.2.30. The CJK host-test helpers `check_efont_tw.py` and `test_cjk_font_width.cpp` mirror parts of its glyph and text-width behavior. Preserve the applicable notices in [third_party/M5GFX-LICENSE.txt](third_party/M5GFX-LICENSE.txt) and [third_party/LovyanGFX-LICENSE.txt](third_party/LovyanGFX-LICENSE.txt). M5GFX identifies LovyanGFX as FreeBSD-licensed. Source: https://github.com/m5stack/M5GFX/tree/0.2.30

M5GFX identifies efont as The Electronic Font Open Laboratory's BSD 3-clause font. The pinned upstream notice is included in [third_party/efont-COPYRIGHT.txt](third_party/efont-COPYRIGHT.txt); the font data itself is fetched separately during a firmware build and is not included in this source candidate. Source: https://github.com/m5stack/M5GFX/blob/0.2.30/src/lgfx/Fonts/efont/COPYRIGHT.txt

Before distributing a firmware binary, identify all actually linked libraries, fonts and other assets and include their applicable license texts, notices and disclaimers with the binary distribution. The M5GFX package also lists components with their own terms; this notice does not assert that every possible build dependency is covered.

Python packages are specified in the lockfiles and installed separately. Review the licenses of the actual dependency set before distributing a bundled image, package, or binary.
