#!/usr/bin/env python3
r"""
drawio_boxify.py  (v3)

Batch-transform a draw.io custom shape library (.xml) of embedded-SVG shapes:

  BEFORE:  [icon]  with the label rendered BELOW it
  AFTER:   [ rounded box | icon on the left | label on the right, inside ]

The rounded bounding box is drawn INTO the SVG. The label is left as a normal
draw.io cell label, and the <object> wrapper (placeholders, imName, imNotes,
tooltip, metaEdit) is preserved verbatim -- so the Edit Data dialog keeps
working exactly as before.

All tunable defaults live in the CONFIGURATION block below; every one of them
can still be overridden on the command line.

Usage:
    python drawio_boxify.py Shapes.drawio.xml -o Shapes-boxed.xml

    # also fold the notes field into the visible label
    python drawio_boxify.py Shapes.drawio.xml \
        --label '%imName%<br><font color="#CC0000">%imNotes%</font>'

Standard library only.
"""

import argparse
import base64
import binascii
import html
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET


# ==========================================================================
# CONFIGURATION -- adjust these defaults to taste.
# Every value here is also exposed as a command-line flag.
# ==========================================================================

# ---- Overall box geometry -------------------------------------------------
WIDTH = 280.0           # full shape width  (px)
HEIGHT = 110.0          # full shape height (px)
CORNER_RADIUS = 16.0    # rounded-corner radius; 0 = square corners

# ---- Box appearance -------------------------------------------------------
BOX_FILL = "#FFFFFF"    # interior fill; "none" for transparent
BOX_STROKE = "#666666"  # border colour
BOX_STROKE_WIDTH = 1.5  # border thickness

# ---- Icon placement (inside the box, left-hand side) ----------------------
ICON_X = 18.0           # left inset of the icon slot
ICON_WIDTH = 60.0       # icon slot width  -- art is scaled to fit,
ICON_HEIGHT = 60.0      # icon slot height    preserving aspect ratio

# ---- Label placement ------------------------------------------------------
TEXT_X = 115.0          # left inset of the label (becomes spacingLeft)
LABEL = None            # None = keep each shape's existing label.
                        # e.g. r'%imName%<br>%imNotes%'

# ---- Entry selection ------------------------------------------------------
SKIP_PREFIXES = ["Logo - ", "Icon - "]   # names starting with these are
                                         # passed through untouched
SKIP_GROUPS = True      # skip grouped / multi-object entries (recommended:
                        # boxifying a group resizes the group and boxes one
                        # arbitrary child)
RETITLE = True          # backfill a missing library title from imName

# ---- Style tokens applied to converted shapes ----------------------------
# Keys listed here are forced to these values; any other token already on the
# shape is preserved as-is.
BOX_STYLE_TOKENS = {
    "verticalLabelPosition": "middle",
    "verticalAlign": "middle",
    "labelPosition": "center",
    "align": "left",
    "labelBackgroundColor": "none",
    "imageAspect": "0",
    "whiteSpace": "wrap",
    "html": "1",
}

# ==========================================================================
# End of configuration.
# ==========================================================================


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


# --------------------------------------------------------------------------
# data: URI handling
#
# draw.io commonly writes  "data:image/svg+xml,<base64>"  -- i.e. a base64
# payload WITHOUT the ";base64" marker. So sniff rather than trust the header.
# --------------------------------------------------------------------------
def decode_data_uri(uri):
    header, _, payload = uri.partition(",")
    if ";base64" in header:
        return base64.b64decode(payload).decode("utf-8", "replace")
    unquoted = urllib.parse.unquote(payload)
    if unquoted.lstrip().startswith("<"):
        return unquoted
    try:
        decoded = base64.b64decode(payload, validate=True).decode("utf-8", "replace")
        if decoded.lstrip().startswith("<"):
            return decoded
    except (binascii.Error, ValueError):
        pass
    raise ValueError("could not decode data URI payload")


def encode_data_uri(svg_text):
    b64 = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
    return "data:image/svg+xml," + b64


# --------------------------------------------------------------------------
# SVG geometry
# --------------------------------------------------------------------------
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
    w = _num(root.get("width"), 100.0)
    h = _num(root.get("height"), 100.0)
    return (0.0, 0.0, w or 100.0, h or 100.0)


def _new_canvas(cfg):
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


def boxify_raster(uri, cfg):
    """Wrap a PNG/JPEG data URI into the same rounded-box canvas."""
    out = _new_canvas(cfg)
    ET.SubElement(
        out,
        "{%s}image" % SVG_NS,
        {
            "x": "%g" % cfg.icon_x,
            "y": "%g" % ((cfg.height - cfg.icon_height) / 2.0),
            "width": "%g" % cfg.icon_width,
            "height": "%g" % cfg.icon_height,
            "preserveAspectRatio": "xMidYMid meet",
            "{%s}href" % XLINK_NS: uri,
        },
    )
    return ET.tostring(out, encoding="unicode")


def boxify_svg(svg_text, cfg):
    src = ET.fromstring(svg_text)
    min_x, min_y, src_w, src_h = svg_source_box(src)

    scale = min(cfg.icon_width / src_w, cfg.icon_height / src_h)
    draw_w, draw_h = src_w * scale, src_h * scale
    tx = cfg.icon_x + (cfg.icon_width - draw_w) / 2.0
    ty = (cfg.height - draw_h) / 2.0

    out = _new_canvas(cfg)
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


# --------------------------------------------------------------------------
# style token surgery -- preserve everything we don't explicitly change
# --------------------------------------------------------------------------
def rewrite_style(style, data_uri, cfg):
    forced = dict(BOX_STYLE_TOKENS)
    forced["spacingLeft"] = "%g" % cfg.text_x

    tokens, seen = [], set()
    for tok in style.split(";"):
        if not tok:
            continue
        key = tok.split("=", 1)[0]
        seen.add(key)
        if key == "image":
            tokens.append("image=" + data_uri)
        elif key in forced:
            tokens.append("%s=%s" % (key, forced[key]))
        else:
            tokens.append(tok)

    for key, val in forced.items():
        if key not in seen:
            tokens.append("%s=%s" % (key, val))

    return ";".join(tokens) + ";"


# --------------------------------------------------------------------------
# entry transform
# --------------------------------------------------------------------------
IMAGE_RE = re.compile(r'image=(data:[^;"]+)')


def clean_name(text):
    """Flatten an imName into a single-line plain-text title.

    imName values are entity-escaped inside an XML attribute that is itself
    escaped inside the library JSON, so they can arrive still carrying
    markup such as '&#10;' (newline) or '&nbsp;'. Decode repeatedly, then
    collapse all whitespace -- including NBSP -- into single spaces.
    """
    prev = None
    while text != prev:
        prev, text = text, html.unescape(text)
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def entry_name(entry):
    """Display name: the library title, else the imName on the <object>."""
    title = entry.get("title")
    if title:
        return clean_name(title)
    im = re.search(r'imName="([^"]*)"', html.unescape(entry.get("xml", "") or ""))
    return clean_name(im.group(1)) if im else ""


def is_group_entry(cell):
    """True if the entry is a composite: a group cell, or more than one
    <object>/vertex. Boxifying these is wrong -- the first image= and the
    first <mxGeometry> belong to arbitrary children, not to the shape as a
    whole, so the group gets resized and a random child gets a box."""
    try:
        root = ET.fromstring(cell)
    except ET.ParseError:
        # fall back to counting if it won't parse
        return len(re.findall(r"<object\b", cell)) > 1 or 'style="group' in cell

    objects, vertices = 0, 0
    for el in root.iter():
        if el.tag == "object":
            objects += 1
        if el.tag == "mxCell":
            style = el.get("style") or ""
            if re.search(r"(^|;)\s*group\s*(;|$)", style):
                return True
            if el.get("vertex") == "1":
                vertices += 1
    return objects > 1 or vertices > 1


def transform_entry(entry, cfg):
    """Return (new_entry, status). Preserves <object> wrapper verbatim."""
    raw = entry.get("xml")
    if not raw:
        return entry, "no-xml"

    name = entry_name(entry)
    for prefix in cfg.skip_prefix:
        if name.lower().startswith(prefix.lower()):
            return entry, "skipped-prefix"

    cell = html.unescape(raw)

    if cfg.skip_groups and is_group_entry(cell):
        return entry, "group"

    m = IMAGE_RE.search(cell)
    if not m:
        return entry, "no-image"
    uri = m.group(1)

    try:
        if uri.startswith("data:image/svg"):
            new_svg = boxify_svg(decode_data_uri(uri), cfg)
        else:
            # raster (PNG/JPEG): re-embed as-is inside the boxed canvas.
            # draw.io omits the ";base64" marker, so add it back for the
            # nested <image> href, which must be a standards-compliant URI.
            head, _, payload = uri.partition(",")
            href = uri if ";base64" in head else "%s;base64,%s" % (head, payload)
            new_svg = boxify_raster(href, cfg)
    except Exception as exc:
        return entry, "error: %s" % exc

    new_uri = encode_data_uri(new_svg)

    # rewrite only the style attribute that contains the image
    def fix_style(sm):
        return 'style="%s"' % html.escape(
            rewrite_style(html.unescape(sm.group(1)), new_uri, cfg), quote=True
        )

    new_cell = re.sub(r'style="([^"]*image=data:[^"]*)"', fix_style, cell, count=1)

    # Resize geometry. Attribute order is NOT guaranteed -- draw.io writes
    # both width-then-height and height-then-width -- so rewrite each
    # attribute independently rather than matching them as a pair.
    def fix_geometry(gm):
        tag = gm.group(0)
        for attr, val in (("width", cfg.width), ("height", cfg.height)):
            if re.search(r'\b%s="' % attr, tag):
                tag = re.sub(r'\b%s="[^"]*"' % attr, '%s="%g"' % (attr, val), tag, count=1)
            else:
                tag = re.sub(r"(<mxGeometry\b)", r'\1 %s="%g"' % (attr, val), tag, count=1)
        return tag

    new_cell = re.sub(r"<mxGeometry\b[^>]*>", fix_geometry, new_cell, count=1)

    # optional label override (keeps placeholders working)
    if cfg.label is not None:
        new_cell = re.sub(
            r'(<object\b[^>]*?\blabel=")[^"]*(")',
            lambda mm: mm.group(1) + html.escape(cfg.label, quote=True) + mm.group(2),
            new_cell,
            count=1,
        )

    out = dict(entry)
    # The library file is XML on the outside, JSON on the inside. draw.io
    # XML-parses the file FIRST, then JSON.parses the text content -- so the
    # cell markup must be entity-escaped or the outer XML parse blows up.
    out["xml"] = html.escape(new_cell, quote=False)
    out["w"] = _tidy(cfg.width)
    out["h"] = _tidy(cfg.height)
    out["aspect"] = "fixed"
    return out, "ok"


# --------------------------------------------------------------------------
def transform_library(in_path, out_path, cfg):
    text = open(in_path, encoding="utf-8").read()
    m = re.search(r"<mxlibrary[^>]*>(.*)</mxlibrary>", text, re.S)
    if not m:
        sys.exit("No <mxlibrary> element found in %s" % in_path)

    entries = json.loads(m.group(1).strip())
    out_entries, counts, retitled = [], {}, []

    for entry in entries:
        new_entry, status = transform_entry(entry, cfg)

        # Backfill the library title from imName so untitled entries show a
        # real name in the shape palette instead of a blank.
        if cfg.retitle and not new_entry.get("title"):
            derived = entry_name(new_entry)
            if derived:
                new_entry = dict(new_entry)
                new_entry["title"] = derived
                retitled.append(derived)

        out_entries.append(new_entry)
        key = status if status == "ok" else status.split(":")[0]
        counts[key] = counts.get(key, 0) + 1

        # many entries carry title:null and hold the real name in imName
        title = entry_name(entry) or "(untitled)"
        if status == "ok":
            print("  + %s" % title)
        else:
            print("  . %s  [%s]" % (title, status))

    body = json.dumps(out_entries, indent=2)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("<mxlibrary>%s</mxlibrary>" % body)

    print("\n" + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    if retitled:
        print("retitled %d untitled entries from imName" % len(retitled))
    print("wrote %s" % out_path)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("input")
    p.add_argument("-o", "--output", default=None)

    p.add_argument("--width", type=float, default=WIDTH)
    p.add_argument("--height", type=float, default=HEIGHT)
    p.add_argument("--corner-radius", type=float, default=CORNER_RADIUS)

    p.add_argument("--box-fill", default=BOX_FILL)
    p.add_argument("--box-stroke", default=BOX_STROKE)
    p.add_argument("--box-stroke-width", type=float, default=BOX_STROKE_WIDTH)

    p.add_argument("--icon-x", type=float, default=ICON_X)
    p.add_argument("--icon-width", type=float, default=ICON_WIDTH)
    p.add_argument("--icon-height", type=float, default=ICON_HEIGHT)

    p.add_argument("--text-x", type=float, default=TEXT_X,
                   help="left inset of the label (spacingLeft)")
    p.add_argument("--label", default=LABEL,
                   help="override the <object> label, e.g. "
                        "'%%imName%%<br>%%imNotes%%'")

    p.add_argument("--skip-prefix", action="append", default=None, metavar="PREFIX",
                   help="leave entries whose name starts with PREFIX untouched "
                        "(case-insensitive; repeatable). Defaults to %s."
                        % ", ".join(repr(s) for s in SKIP_PREFIXES))
    p.add_argument("--no-skip-prefix", action="store_true",
                   help="disable the default prefix skipping")
    p.add_argument("--no-skip-groups", dest="skip_groups", action="store_false",
                   default=SKIP_GROUPS,
                   help="also transform grouped/multi-object entries "
                        "(not recommended)")
    p.add_argument("--no-retitle", dest="retitle", action="store_false",
                   default=RETITLE,
                   help="do not backfill missing library titles from imName")

    cfg = p.parse_args()
    if cfg.no_skip_prefix:
        cfg.skip_prefix = []
    elif cfg.skip_prefix is None:
        cfg.skip_prefix = list(SKIP_PREFIXES)

    out = cfg.output or re.sub(r"\.xml$", "", cfg.input) + "-boxed.xml"
    transform_library(cfg.input, out, cfg)


if __name__ == "__main__":
    main()
