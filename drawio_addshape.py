#!/usr/bin/env python3
r"""
drawio_addshape.py  (v2)

Add a source SVG or PNG to an existing draw.io shape library, as either:

  icon  -- the standard 60x60 icon, label rendered BELOW  (default)
  box   -- the boxified 320x130 form: rounded box, icon left, label right
  both  -- append both variants

Applies the standard style settings and the full metadata configuration
(imName / imNotes / tooltip / placeholders / metaEdit), so the new shape
behaves identically to the rest of the library: double-click Edit Data,
%imName% label substitution, and the <h3> tooltip all work.

All tunable defaults live in the CONFIGURATION block below; every one of them
can still be overridden on the command line.

Usage
-----
  # single file, standard icon
  python drawio_addshape.py Shapes.drawio.xml logo.svg

  # boxified variant, explicit name and notes
  python drawio_addshape.py Shapes.drawio.xml logo.svg \
      --mode box --name "Contoso API" --notes "Edge gateway"

  # Azure service shape with the extended metadata profile
  python drawio_addshape.py Shapes.drawio.xml aks.svg --profile service

  # bulk: every icon in a folder
  python drawio_addshape.py Shapes.drawio.xml .\icons\*.svg --mode both

  # start a brand new library
  python drawio_addshape.py NewLib.xml logo.svg --create

The library is rewritten in place; a .bak copy is made unless --no-backup.
Standard library only.
"""

import argparse
import base64
import glob
import html
import json
import os
import re
import shutil
import sys
import urllib.parse
import xml.etree.ElementTree as ET


# ==========================================================================
# CONFIGURATION -- adjust these defaults to taste.
# Every value here is also exposed as a command-line flag.
# ==========================================================================

# ---- What gets added ------------------------------------------------------
MODE = "icon"           # "icon", "box", or "both"
PROFILE = "basic"       # metadata profile: "basic" or "service" (see PROFILES)
NOTES = ""              # initial imNotes value
BOX_SUFFIX = ""         # appended to the box variant's name in --mode both,
                        # e.g. " (boxed)"

# ---- Standard icon variant ------------------------------------------------
ICON_W = 60             # icon shape width  (px)
ICON_H = 60             # icon shape height (px)

# ---- Boxified variant: overall geometry -----------------------------------
# Keep these in sync with drawio_boxify.py so both tools agree.
BOX_W = 280.0           # full shape width  (px)
BOX_H = 110.0           # full shape height (px)
CORNER_RADIUS = 16.0    # rounded-corner radius; 0 = square corners

# ---- Boxified variant: box appearance -------------------------------------
BOX_FILL = "#FFFFFF"    # interior fill; "none" for transparent
BOX_STROKE = "#666666"  # border colour
BOX_STROKE_WIDTH = 1.5  # border thickness

# ---- Boxified variant: icon placement (inside the box, left-hand side) ----
ICON_X = 18.0           # left inset of the icon slot
ICON_WIDTH = 60.0       # icon slot width  -- art is scaled to fit,
ICON_HEIGHT = 60.0      # icon slot height    preserving aspect ratio

# ---- Boxified variant: label placement ------------------------------------
TEXT_X = 115.0          # left inset of the label (becomes spacingLeft)

# ---- File handling --------------------------------------------------------
BACKUP = True           # write a .bak copy before rewriting the library

# ---- Style tokens ---------------------------------------------------------
# Shared by both variants.
BASE_STYLE = [
    "shape=image",
    "aspect=fixed",
    "fontSize=10",
    "resizable=0",
    "rotatable=0",
    "rotation=0",
    "resizeWidth=1",
    "resizeHeight=1",
    "metaEdit=1",
]

# Standard icon: label sits BELOW the image.
ICON_STYLE = [
    "verticalLabelPosition=bottom",
    "verticalAlign=top",
    "labelBackgroundColor=default",
    "imageAspect=1",
]

# Boxified: label sits INSIDE the box, to the right of the icon.
BOX_STYLE = [
    "verticalLabelPosition=middle",
    "verticalAlign=middle",
    "labelPosition=center",
    "align=left",
    "labelBackgroundColor=none",
    "imageAspect=0",
    "whiteSpace=wrap",
    "html=1",
]

# Lets draw.io recolour embedded SVGs via Edit Style. SVG only -- it is
# meaningless on a raster and the library omits it for the PNG entry.
SVG_ONLY_TOKENS = ["editableCssRules=.*"]

# ---- Metadata configuration written onto the <object> wrapper -------------
#
# The library uses two profiles:
#   basic   -- imName + imNotes             (generic shapes)
#   service -- adds plan/shared/count/props (Azure PaaS services)
# Field order is preserved to match the existing entries.
LABEL = "%imName%"

PROFILES = {
    "basic": {
        "fields": ["imName", "imNotes"],
        "tooltip": "<h3>%imName%</h3>\n<b>Notes: </b>%imNotes%",
    },
    "service": {
        "fields": ["imName", "imCount", "imNotes", "imProperties",
                   "imServicePlan", "imShared"],
        "tooltip": (
            "<h3>%imName%</h3>\n"
            "<b>Plan: </b>%imServicePlan%<br>\n"
            "<b>Shared/Dedicated: </b>%imShared%<br>\n"
            "<b>Count: </b>%imCount%<br>\n"
            "<b>Properties: </b>%imProperties%<br>\n"
            "<hr>\n"
            "Notes: %imNotes%"
        ),
    },
}

# ---- Accepted source types ------------------------------------------------
MIME_BY_EXT = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}

# ==========================================================================
# End of configuration.
# ==========================================================================


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


# ==========================================================================
# data: URI helpers
#
# draw.io writes "data:image/svg+xml,<base64>" -- base64 payload with NO
# ";base64" marker. Match that convention for the style token, but emit a
# standards-compliant URI for any nested <image href>.
# ==========================================================================
def to_drawio_uri(payload_bytes, mime):
    return "data:%s,%s" % (mime, base64.b64encode(payload_bytes).decode("ascii"))


def to_standard_uri(payload_bytes, mime):
    return "data:%s;base64,%s" % (mime, base64.b64encode(payload_bytes).decode("ascii"))


def decode_data_uri(uri):
    header, _, payload = uri.partition(",")
    if ";base64" in header:
        return base64.b64decode(payload)
    unquoted = urllib.parse.unquote(payload)
    if unquoted.lstrip().startswith("<"):
        return unquoted.encode("utf-8")
    return base64.b64decode(payload)


# ==========================================================================
# SVG geometry / boxify
# ==========================================================================
def _tidy(val):
    """Write whole numbers as ints so the JSON reads 320 rather than 320.0."""
    return int(val) if float(val).is_integer() else val


def _num(val, default=None):
    if val is None:
        return default
    m = re.match(r"\s*(-?[\d.eE+]+)", str(val))
    return float(m.group(1)) if m else default


def svg_source_box(root):
    vb = root.get("viewBox")
    if vb:
        parts = [float(p) for p in re.split(r"[ ,]+", vb.strip()) if p]
        if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
            return tuple(parts)
    w = _num(root.get("width"), 100.0) or 100.0
    h = _num(root.get("height"), 100.0) or 100.0
    return (0.0, 0.0, w, h)


def new_canvas(cfg):
    out = ET.Element(
        "{%s}svg" % SVG_NS,
        {
            "width": "%g" % cfg.width,
            "height": "%g" % cfg.height,
            "viewBox": "0 0 %g %g" % (cfg.width, cfg.height),
        },
    )
    inset = cfg.box_stroke_width / 2.0
    ET.SubElement(
        out,
        "{%s}rect" % SVG_NS,
        {
            "x": "%g" % inset,
            "y": "%g" % inset,
            "width": "%g" % (cfg.width - cfg.box_stroke_width),
            "height": "%g" % (cfg.height - cfg.box_stroke_width),
            "rx": "%g" % cfg.corner_radius,
            "ry": "%g" % cfg.corner_radius,
            "fill": cfg.box_fill,
            "stroke": cfg.box_stroke,
            "stroke-width": "%g" % cfg.box_stroke_width,
        },
    )
    return out


def boxify_svg(svg_text, cfg):
    src = ET.fromstring(svg_text)
    min_x, min_y, src_w, src_h = svg_source_box(src)

    scale = min(cfg.icon_width / src_w, cfg.icon_height / src_h)
    draw_w, draw_h = src_w * scale, src_h * scale
    tx = cfg.icon_x + (cfg.icon_width - draw_w) / 2.0
    ty = (cfg.height - draw_h) / 2.0

    out = new_canvas(cfg)
    g = ET.SubElement(
        out,
        "{%s}g" % SVG_NS,
        {
            "transform": "translate(%g,%g) scale(%g) translate(%g,%g)"
            % (tx, ty, scale, -min_x, -min_y)
        },
    )
    for child in list(src):
        g.append(child)
    return ET.tostring(out, encoding="unicode")


def boxify_raster(raw_bytes, mime, cfg):
    out = new_canvas(cfg)
    ET.SubElement(
        out,
        "{%s}image" % SVG_NS,
        {
            "x": "%g" % cfg.icon_x,
            "y": "%g" % ((cfg.height - cfg.icon_height) / 2.0),
            "width": "%g" % cfg.icon_width,
            "height": "%g" % cfg.icon_height,
            "preserveAspectRatio": "xMidYMid meet",
            "{%s}href" % XLINK_NS: to_standard_uri(raw_bytes, mime),
        },
    )
    return ET.tostring(out, encoding="unicode")


# ==========================================================================
# Source loading
# ==========================================================================
def load_source(path):
    ext = os.path.splitext(path)[1].lower()
    mime = MIME_BY_EXT.get(ext)
    if not mime:
        raise ValueError("unsupported file type '%s'" % ext)
    with open(path, "rb") as fh:
        raw = fh.read()
    if mime == "image/svg+xml":
        text = raw.decode("utf-8-sig", "replace")
        ET.fromstring(text)  # fail early on malformed SVG
        return mime, raw, text
    return mime, raw, None


def clean_name(text):
    prev = None
    while text != prev:
        prev, text = text, html.unescape(text)
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def name_from_path(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[_\-]+", " ", stem)
    return clean_name(stem)


# ==========================================================================
# Entry construction
# ==========================================================================
def build_style(mime, data_uri, mode, cfg):
    tokens = list(BASE_STYLE)
    tokens += ICON_STYLE if mode == "icon" else BOX_STYLE
    if mime == "image/svg+xml":
        tokens += SVG_ONLY_TOKENS
    if mode == "box":
        tokens.append("spacingLeft=%g" % cfg.text_x)
    tokens.append("image=" + data_uri)
    return ";".join(tokens) + ";"


def build_entry(name, notes, mime, raw, svg_text, mode, cfg):
    if mode == "icon":
        payload, out_mime = raw, mime
        w, h = cfg.icon_w, cfg.icon_h
    else:
        if mime == "image/svg+xml":
            boxed = boxify_svg(svg_text, cfg)
        else:
            boxed = boxify_raster(raw, mime, cfg)
        payload, out_mime = boxed.encode("utf-8"), "image/svg+xml"
        w, h = cfg.width, cfg.height

    style = build_style(mime, to_drawio_uri(payload, out_mime), mode, cfg)

    # Build via ElementTree so all attribute escaping is handled correctly,
    # then post-process the tooltip newline into the &#10; entity the
    # library uses (ET would otherwise emit a literal newline).
    model = ET.Element("mxGraphModel")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    profile = PROFILES[cfg.profile]
    attrs = {"label": LABEL}
    for field in profile["fields"]:
        attrs[field] = name if field == "imName" else (notes if field == "imNotes" else "")
    attrs["tooltip"] = profile["tooltip"]
    attrs["placeholders"] = "1"
    attrs["id"] = "2"
    obj = ET.SubElement(root, "object", attrs)

    cell = ET.SubElement(
        obj, "mxCell", {"style": style, "vertex": "1", "parent": "1"}
    )
    ET.SubElement(
        cell,
        "mxGeometry",
        {"width": "%g" % w, "height": "%g" % h, "as": "geometry"},
    )

    cell_xml = ET.tostring(model, encoding="unicode")
    cell_xml = cell_xml.replace("&#10;", "\n").replace("\n", "&#10;")

    return {
        # The library is XML outside, JSON inside: draw.io XML-parses the
        # file first, so the cell markup must be entity-escaped.
        "xml": html.escape(cell_xml, quote=False),
        "w": _tidy(w),
        "h": _tidy(h),
        "aspect": "fixed",
        "title": name,
    }


# ==========================================================================
# Library IO
# ==========================================================================
def read_library(path, create=False):
    if not os.path.exists(path):
        if not create:
            sys.exit("Library not found: %s  (use --create to start a new one)" % path)
        return []
    raw = open(path, encoding="utf-8").read()
    m = re.search(r"<mxlibrary[^>]*>(.*)</mxlibrary>", raw, re.S)
    if not m:
        sys.exit("No <mxlibrary> element found in %s" % path)
    body = m.group(1).strip()
    return json.loads(body) if body else []


def write_library(path, entries, backup=True):
    made_backup = False
    if backup and os.path.exists(path):
        shutil.copy2(path, path + ".bak")
        made_backup = True
    body = json.dumps(entries, indent=2)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("<mxlibrary>%s</mxlibrary>" % body)
    return made_backup


def existing_names(entries):
    names = set()
    for e in entries:
        t = e.get("title")
        if t:
            names.add(clean_name(t))
            continue
        im = re.search(r'imName="([^"]*)"', html.unescape(e.get("xml", "") or ""))
        if im:
            names.add(clean_name(im.group(1)))
    return names


def validate(path):
    """Re-read the written file the way draw.io does, and fail loudly."""
    raw = open(path, encoding="utf-8").read()
    ET.fromstring(raw)
    body = re.search(r"<mxlibrary[^>]*>(.*)</mxlibrary>", raw, re.S).group(1)
    entries = json.loads(body)
    for e in entries:
        if "xml" in e:
            ET.fromstring(html.unescape(e["xml"]))
    return len(entries)


# ==========================================================================
def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("library", help="existing .xml shape library to append to")
    p.add_argument("sources", nargs="+", help="source .svg / .png files (globs ok)")

    p.add_argument("--mode", choices=["icon", "box", "both"], default=MODE,
                   help="which variant to add (default: %s)" % MODE)
    p.add_argument("--name", default=None,
                   help="shape name; defaults to the filename. Only valid "
                        "with a single source file.")
    p.add_argument("--notes", default=NOTES,
                   help="initial imNotes value (default: empty)")
    p.add_argument("--profile", choices=sorted(PROFILES), default=PROFILE,
                   help="metadata profile: 'basic' (imName/imNotes) or "
                        "'service' (adds plan, shared, count, properties). "
                        "Default: %s" % PROFILE)
    p.add_argument("--box-suffix", default=BOX_SUFFIX,
                   help="appended to the name for the box variant in "
                        "--mode both, e.g. ' (boxed)'")

    p.add_argument("--replace", action="store_true",
                   help="replace an existing entry of the same name")
    p.add_argument("--create", action="store_true",
                   help="create the library if it does not exist")
    p.add_argument("--no-backup", dest="backup", action="store_false",
                   default=BACKUP,
                   help="do not write a .bak copy")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would happen, write nothing")

    # icon variant geometry
    p.add_argument("--icon-w", type=float, default=ICON_W,
                   help="standard icon width (default: %g)" % ICON_W)
    p.add_argument("--icon-h", type=float, default=ICON_H,
                   help="standard icon height (default: %g)" % ICON_H)

    # box variant geometry -- keep in sync with drawio_boxify.py
    p.add_argument("--width", type=float, default=BOX_W)
    p.add_argument("--height", type=float, default=BOX_H)
    p.add_argument("--corner-radius", type=float, default=CORNER_RADIUS)
    p.add_argument("--box-fill", default=BOX_FILL)
    p.add_argument("--box-stroke", default=BOX_STROKE)
    p.add_argument("--box-stroke-width", type=float, default=BOX_STROKE_WIDTH)
    p.add_argument("--icon-x", type=float, default=ICON_X)
    p.add_argument("--icon-width", type=float, default=ICON_WIDTH)
    p.add_argument("--icon-height", type=float, default=ICON_HEIGHT)
    p.add_argument("--text-x", type=float, default=TEXT_X,
                   help="left inset of the label (spacingLeft)")

    cfg = p.parse_args()

    files = []
    for pattern in cfg.sources:
        hits = glob.glob(pattern)
        files.extend(hits if hits else ([pattern] if os.path.exists(pattern) else []))
        if not hits and not os.path.exists(pattern):
            print("  ! no match: %s" % pattern, file=sys.stderr)
    if not files:
        sys.exit("No source files found.")
    if cfg.name and len(files) > 1:
        sys.exit("--name cannot be used with multiple source files.")

    entries = read_library(cfg.library, create=cfg.create)
    have = existing_names(entries)
    modes = ["icon", "box"] if cfg.mode == "both" else [cfg.mode]

    added = skipped = replaced = 0
    for path in sorted(files):
        try:
            mime, raw, svg_text = load_source(path)
        except Exception as exc:
            print("  ! %s: %s" % (os.path.basename(path), exc), file=sys.stderr)
            continue

        base = cfg.name or name_from_path(path)
        for mode in modes:
            name = base + (cfg.box_suffix if mode == "box" and cfg.mode == "both" else "")

            if name in have:
                if not cfg.replace:
                    print("  . %-28s [exists, skipped]" % name)
                    skipped += 1
                    continue
                entries = [
                    e for e in entries
                    if clean_name(e.get("title") or "") != name
                ]
                replaced += 1

            entry = build_entry(name, cfg.notes, mime, raw, svg_text, mode, cfg)
            entries.append(entry)
            have.add(name)
            added += 1
            print("  + %-28s [%s %gx%g from %s]"
                  % (name, mode, entry["w"], entry["h"], os.path.basename(path)))

    if cfg.dry_run:
        print("\ndry run -- nothing written")
        return

    made_backup = write_library(cfg.library, entries, backup=cfg.backup)
    total = validate(cfg.library)
    print("\nadded=%d, replaced=%d, skipped=%d" % (added, replaced, skipped))
    print("library now has %d entries -- validated OK" % total)
    print("wrote %s%s"
          % (cfg.library, "  (backup: %s.bak)" % cfg.library if made_backup else ""))


if __name__ == "__main__":
    main()
