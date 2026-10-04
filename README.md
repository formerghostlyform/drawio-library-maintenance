# draw.io library maintenance

Two Python scripts for maintaining draw.io / diagrams.net custom shape libraries:

| Script | Purpose |
| --- | --- |
| `drawio_addshape.py` | Add source images as icons, boxed shapes, or both. |
| `drawio_boxify.py` | Convert existing image shapes into rounded boxes with the icon on the left and the label inside on the right. |

## Requirements

- Python 3; no third-party packages are needed.
- A custom shape library XML file containing an `<mxlibrary>` element with a JSON array of entries. These scripts operate on libraries, not ordinary diagram files.
- For adding shapes: SVG, PNG, JPG/JPEG, or GIF source files.

Run the commands below from this directory. On Windows, use `py` instead of `python` if that is how your Python installation is configured. Examples use PowerShell-compatible quoting.

## Add shapes

Create a library with one icon:

```powershell
python drawio_addshape.py Shapes.xml service.svg --create --name "My Service"
```

Append images to an existing library (quoted wildcard patterns are expanded by the script):

```powershell
python drawio_addshape.py Shapes.xml "icons/*.svg" "icons/*.png"
```

Add a boxed shape with service metadata:

```powershell
python drawio_addshape.py Shapes.xml service.svg --mode box --profile service --notes "Production service"
```

Add both variants with distinct names:

```powershell
python drawio_addshape.py Shapes.xml service.svg --mode both --box-suffix " (boxed)"
```

**Use a nonempty `--box-suffix` with `--mode both`.** Its default is empty, so both variants have the same name: the second is skipped by default, or replaces the first with `--replace`.

Preview additions without writing:

```powershell
python drawio_addshape.py Shapes.xml "icons/*.svg" --dry-run
```

### Add-shape options

| Option | Behavior / default |
| --- | --- |
| `--mode icon\|box\|both` | Defaults to `icon`: a 60×60 image with its label below. Boxes default to 280×110. |
| `--name NAME` | Overrides the name; valid only with one resolved source file. Otherwise the filename stem is used, with hyphens and underscores replaced by spaces. |
| `--notes TEXT` | Initial `imNotes` value; empty by default. |
| `--profile basic\|service` | Defaults to `basic`. See metadata below. |
| `--box-suffix TEXT` | Added to the box name only in `both` mode; empty by default. |
| `--replace` | Replaces entries with matching titles instead of skipping existing names. Matching is case-sensitive. |
| `--create` | Starts an empty library if the destination does not exist. |
| `--no-backup` | Disables the default backup to `<library>.bak`. Each write overwrites that backup. |
| `--dry-run` | Reports planned changes without writing the library or backup. Use `--create` as well when previewing a new library. |
| `--icon-w`, `--icon-h` | Icon variant dimensions; both default to `60`. |

New entries use `%imName%` as the label and enable placeholders and metadata editing. The `basic` profile includes `imName` and `imNotes`. The `service` profile also includes `imCount`, `imProperties`, `imServicePlan`, and `imShared`, initially empty. Tooltips reference the profile's fields.

The script rewrites the destination library and validates the outer XML, JSON, and embedded cell XML afterward. Unsupported or unreadable sources are reported and skipped. Name detection falls back to `imName` for untitled entries, but replacement removes entries by title; inspect older untitled entries before relying on `--replace`.

## Boxify an existing library

```powershell
python drawio_boxify.py Shapes.xml -o Shapes-boxed.xml
```

Without `-o` / `--output`, the output name is the input path with its trailing `.xml` removed and `-boxed.xml` appended. The input remains unchanged unless you explicitly select the same output path. Existing output files are overwritten; this script does not create backups.

Include notes in the visible label:

```powershell
python drawio_boxify.py Shapes.xml --label '%imName%<br>%imNotes%'
```

Convert entries normally excluded by name:

```powershell
python drawio_boxify.py Shapes.xml --no-skip-prefix
```

### Boxify options and behavior

| Option | Behavior / default |
| --- | --- |
| `-o`, `--output PATH` | Destination library file. |
| `--label TEXT` | Overrides an existing `<object>` label; otherwise labels are retained. Supports placeholder text and HTML. |
| `--skip-prefix PREFIX` | Repeatable, case-insensitive prefix filter. Supplying it replaces the default filters, `Logo - ` and `Icon - `. |
| `--no-skip-prefix` | Disables all prefix filtering, including explicitly supplied prefixes. |
| `--no-skip-groups` | Allows grouped / multi-object entries to be processed. This can resize a group and box an arbitrary child; groups are skipped by default. |
| `--no-retitle` | Disables filling missing library titles from `imName`. Title filling applies even to skipped entries. |

The script handles embedded SVG and raster image data URIs, changes image styling and dimensions, and preserves existing metadata and unrelated style tokens. Entries without cell XML or an embedded image are retained. Image conversion failures retain the entry and are reported in the per-entry output and summary; review that output for incomplete conversions.

Run boxification on the original icon library: rerunning it on already boxed shapes can nest the existing box inside another box.

## Shared box appearance options

Both scripts accept these options. Dimensions and offsets are in pixels.

| Option | Default | Purpose |
| --- | --- | --- |
| `--width` | `280` | Overall box width. |
| `--height` | `110` | Overall box height. |
| `--corner-radius` | `16` | Rounded corner radius; `0` gives square corners. |
| `--box-fill` | `#FFFFFF` | Background color; `none` gives a transparent fill. |
| `--box-stroke` | `#666666` | Border color. |
| `--box-stroke-width` | `1.5` | Border thickness. |
| `--icon-x` | `18` | Left inset of the icon slot. |
| `--icon-width` | `60` | Icon slot width. |
| `--icon-height` | `60` | Icon slot height. |
| `--text-x` | `115` | Left inset of the label (`spacingLeft`). |

Icons are centered vertically and scaled to fit the slot while preserving their aspect ratio. For example:

```powershell
python drawio_boxify.py Shapes.xml --width 320 --height 130 --text-x 110 --box-fill "none" --box-stroke "#336699"
```

Defaults can also be edited in each script's `CONFIGURATION` block. Keep the box settings in sync if you want both tools to produce matching shapes. The add-shape script's introductory docstring mentions 320×130, but its configured defaults are 280×110.

## Command reference

```powershell
python drawio_addshape.py --help
python drawio_boxify.py --help
```

After generating a library, open it in draw.io / diagrams.net using **File → Open Library** and inspect the shapes, labels, and metadata.
