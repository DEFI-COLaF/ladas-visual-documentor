#!/usr/bin/env python3
"""
generate.py — build data.js from a folder of ALTO XML + image pairs,
              or from a COCO JSON annotation file + images.

Usage:
    python generate.py [EXAMPLES_DIR] [OUTPUT_JS]

Defaults:
    EXAMPLES_DIR = examples/
    OUTPUT_JS    = data.js

Auto-detection: if EXAMPLES_DIR contains a *.coco.json file, COCO mode is
used; otherwise ALTO XML mode is used.
"""

import sys
import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"

IMAGE_EXTS = [".jpg", ".jpeg", ".JPG", ".JPEG", ".png", ".PNG", ".tif", ".tiff", ".TIF", ".TIFF"]


def find_image(xml_path: Path, folder: Path) -> str | None:
    stem = xml_path.stem
    for ext in IMAGE_EXTS:
        candidate = folder / (stem + ext)
        if candidate.exists():
            return str(candidate)
    return None


def parse_alto(xml_path: Path, image_path: str) -> dict:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    def tag(name):
        return f"{{{ALTO_NS}}}{name}"

    # Build tag-id → label map from <Tags>
    tag_label = {}
    for other_tag in root.iter(tag("OtherTag")):
        tid = other_tag.get("ID")
        label = other_tag.get("LABEL")
        if tid and label:
            tag_label[tid] = label

    # Get page dimensions
    page_el = root.find(f".//{tag('Page')}")
    width = int(page_el.get("WIDTH", 0)) if page_el is not None else 0
    height = int(page_el.get("HEIGHT", 0)) if page_el is not None else 0

    # Get source filename for the title field (fallback to stem)
    src_el = root.find(f".//{tag('fileName')}")
    source_name = src_el.text.strip() if src_el is not None and src_el.text else xml_path.stem

    zones = []
    for tb in root.iter(tag("TextBlock")):
        tid = tb.get("ID", "")
        tagrefs = tb.get("TAGREFS", "")
        hpos = int(tb.get("HPOS", 0))
        vpos = int(tb.get("VPOS", 0))
        w = int(tb.get("WIDTH", 0))
        h = int(tb.get("HEIGHT", 0))

        # Resolve label from TAGREFS (space-separated list; use first match)
        label = None
        for ref in tagrefs.split():
            if ref in tag_label:
                label = tag_label[ref]
                break
        if label is None:
            label = "Unknown"

        # Get polygon points from <Shape><Polygon POINTS="...">
        poly_el = tb.find(f".//{tag('Polygon')}")
        if poly_el is not None:
            points = poly_el.get("POINTS", "")
        else:
            # Fall back to bounding-box corners
            points = f"{hpos} {vpos} {hpos+w} {vpos} {hpos+w} {vpos+h} {hpos} {vpos+h}"

        zones.append({
            "id": tid,
            "label": label,
            "points": points,
            "hpos": hpos,
            "vpos": vpos,
            "width": w,
            "height": h,
        })

    return {
        "id": xml_path.stem,
        "image": image_path,
        "width": width,
        "height": height,
        "title": source_name,
        "zones": zones,
    }


def parse_coco(coco_path: Path, folder: Path, output_parent: Path) -> list[dict]:
    data = json.loads(coco_path.read_text(encoding="utf-8"))

    cat_id_to_name = {c["id"]: c["name"] for c in data.get("categories", [])}

    annotations_by_image: dict[int, list] = {}
    for ann in data.get("annotations", []):
        annotations_by_image.setdefault(ann["image_id"], []).append(ann)

    pages = []
    for img_meta in data.get("images", []):
        img_id = img_meta["id"]
        file_name = img_meta["file_name"]
        img_path = folder / file_name
        if not img_path.exists():
            print(f"  WARNING: image not found: {file_name}, skipping", file=sys.stderr)
            continue

        try:
            rel = os.path.relpath(str(img_path), str(output_parent))
        except ValueError:
            rel = str(img_path)
        rel = rel.replace("\\", "/")

        width = img_meta.get("width", 0)
        height = img_meta.get("height", 0)
        title = img_meta.get("extra", {}).get("name") or file_name

        zones = []
        for ann in annotations_by_image.get(img_id, []):
            label = cat_id_to_name.get(ann["category_id"], "Unknown")
            x, y, w, h = (int(v) for v in ann["bbox"])

            segs = ann.get("segmentation") or []
            if segs and isinstance(segs[0], list) and segs[0]:
                # Flat polygon coords [x1, y1, x2, y2, ...]
                coords = segs[0]
                points = " ".join(str(int(v)) for v in coords)
            else:
                # Fall back to bounding-box corners
                points = f"{x} {y} {x+w} {y} {x+w} {y+h} {x} {y+h}"

            zones.append({
                "id": str(ann["id"]),
                "label": label,
                "points": points,
                "hpos": x,
                "vpos": y,
                "width": w,
                "height": h,
            })

        stem = Path(file_name).stem
        pages.append({
            "id": stem,
            "image": rel,
            "width": width,
            "height": height,
            "title": title,
            "zones": zones,
        })
        print(f"  {file_name}  →  {len(zones)} zones")

    return pages


def main():
    examples_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("examples")
    output_js = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data.js")

    # Auto-detect COCO vs ALTO
    coco_files = sorted(examples_dir.glob("*.coco.json")) or sorted(examples_dir.glob("_annotations*.json"))

    if coco_files:
        coco_path = coco_files[0]
        if len(coco_files) > 1:
            print(f"  Multiple COCO files found; using {coco_path.name}", file=sys.stderr)
        print(f"COCO mode: {coco_path.name}")
        pages = parse_coco(coco_path, examples_dir, output_js.parent)
    else:
        xml_files = sorted(examples_dir.glob("*.xml"))
        if not xml_files:
            print(f"No XML or COCO JSON files found in {examples_dir}", file=sys.stderr)
            sys.exit(1)

        print(f"ALTO mode: {len(xml_files)} XML file(s)")
        pages = []
        for xf in xml_files:
            img = find_image(xf, examples_dir)
            if img is None:
                print(f"  WARNING: no image found for {xf.name}, skipping", file=sys.stderr)
                continue
            try:
                rel = os.path.relpath(img, output_js.parent)
            except ValueError:
                rel = img
            page = parse_alto(xf, rel.replace("\\", "/"))
            pages.append(page)
            print(f"  {xf.name}  →  {len(page['zones'])} zones")

    if not pages:
        print("No pages produced.", file=sys.stderr)
        sys.exit(1)

    js = "// Auto-generated by generate.py — do not edit manually.\n"
    js += "window.PAGES = " + json.dumps(pages, indent=2, ensure_ascii=False) + ";\n"

    output_js.write_text(js, encoding="utf-8")
    print(f"\nWrote {len(pages)} page(s) to {output_js}")


if __name__ == "__main__":
    main()
