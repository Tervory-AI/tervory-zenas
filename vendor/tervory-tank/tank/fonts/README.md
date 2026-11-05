# Fonts for savings PDFs

These fonts are shipped with Tank so PDF downloads never depend on installed
system fonts, external font services or network access. ReportLab embeds only
used glyphs, including a ToUnicode map for searching and copying job titles.

- `NotoSans-Regular.ttf` is the unmodified Noto Sans Regular face. It covers
  Latin, accented Greek and Cyrillic text. See `LICENSE-NotoSans.txt`.
- `SavingsUnicode-Regular.ttf` is a TrueType conversion of the simplified-Chinese
  face of Noto Sans CJK Regular. It also covers Japanese and Korean characters.
  Its outlines come from Noto Sans CJK, under the SIL Open Font License 1.1;
  see `LICENSE-NotoCJK.txt`. The derived font has been named Savings Unicode.

Sources: [Noto fonts](https://github.com/notofonts/noto-fonts) and
[Noto CJK](https://github.com/notofonts/noto-cjk). The source fonts were already
available in the saved cloud coding environment; no runtime downloads are used.
The license files include the upstream copyright and full font licence text.

The conversion preserves all source encoded characters. FontTools converts cubic
outlines to quadratic outlines with a maximum error of one design unit at 1000
units per em; the runtime does not require FontTools. To rebuild, install
`fonttools==4.61.1` in a developer environment and run:

```sh
python tank/fonts/build_font.py /path/to/NotoSansCJK-Regular.ttc
```

The CJK collection uses face 2 (`NotoSansCJKsc-Regular`). Text uses Noto Sans
first, then the embedded CJK font. A character outside both fonts' coverage
is replaced with U+FFFD. One notice in the PDF points readers to the CSV,
which preserves the complete original text.

Source versions and SHA-256 checksums:

- `NotoSans-Regular.ttf`: Version 2.004; ttfautohint (v1.8.3) -l 8 -r 50 -G 200 -x 14 -D latn -f none -a qsq -X ""; SHA-256 `89c3c497f618fdaa0b2d1e98fef93582f28c71debd2c4a8cdf41f190ced2909d`.
- `NotoSansCJK-Regular.ttc`: Version 2.004;hotconv 1.0.118;makeotfexe 2.5.65603; SHA-256 `b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a`.
