#!/usr/bin/env python
"""Render one enclosure layout to a PNG (headless-safe matplotlib).

Usage:
    python scripts/render_geometry.py tests/fixtures/phase4/ultrasonic_alarm.json
    python scripts/render_geometry.py <spec.json> [out.png] [--export-dir DIR]

Draws the deterministic Phase 4 layout: a plan view (shell outline, parts,
cutouts, screw posts) plus X and Y elevations showing the base and lid
stacks. Uses only the layout numbers, so it works without a solid-modelling
engine; --export-dir additionally builds and writes the STLs.

Exit codes: 0 layout ok, 1 geometry errors, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from sparkplan.geometry.enclosure import EnclosureLayout
from sparkplan.geometry.generate import generate_enclosure
from sparkplan.models.buildspec import parse_build_spec
from sparkplan.models.catalog import get_catalog

plt.switch_backend("Agg")  # headless-safe: no display required

_MOUNT_COLOR = {"base": "#4c78a8", "wall": "#f58518", "lid": "#54a24b"}
_FACE_EDGE = {"-x": "left", "+x": "right", "-y": "bottom", "+y": "top"}


def _draw_plan(ax: plt.Axes, layout: EnclosureLayout) -> None:
    cfg = layout.config
    ax.add_patch(
        Rectangle(
            (0, 0),
            layout.outer_x_mm,
            layout.outer_y_mm,
            fill=False,
            linewidth=2,
            edgecolor="black",
        )
    )
    ax.add_patch(
        Rectangle(
            (cfg.wall_mm, cfg.wall_mm),
            layout.inner_x_mm,
            layout.inner_y_mm,
            fill=False,
            linewidth=1,
            linestyle="--",
            edgecolor="gray",
        )
    )
    for part in layout.parts:
        ax.add_patch(
            Rectangle(
                (cfg.wall_mm + part.x_mm, cfg.wall_mm + part.y_mm),
                part.size_x_mm,
                part.size_y_mm,
                alpha=0.55,
                facecolor=_MOUNT_COLOR.get(part.mount.value, "gray"),
                edgecolor="black",
                linewidth=0.5,
            )
        )
        ax.text(
            cfg.wall_mm + part.x_mm + part.size_x_mm / 2.0,
            cfg.wall_mm + part.y_mm + part.size_y_mm / 2.0,
            part.instance_id,
            ha="center",
            va="center",
            fontsize=7,
        )
    for opening in layout.openings:
        x = cfg.wall_mm + opening.cx_mm - opening.width_mm / 2.0
        y = cfg.wall_mm + opening.cy_mm - opening.height_mm / 2.0
        if opening.face in _FACE_EDGE:
            # wall cutout: mark it on the wall band it pierces
            edge = _FACE_EDGE[opening.face]
            if edge in ("left", "right"):
                wx = 0.0 if edge == "left" else layout.outer_x_mm
                ax.plot(
                    [wx, wx],
                    [y, y + opening.height_mm],
                    color="red",
                    linewidth=3,
                    solid_capstyle="butt",
                )
            else:
                wy = 0.0 if edge == "bottom" else layout.outer_y_mm
                ax.plot(
                    [x, x + opening.width_mm],
                    [wy, wy],
                    color="red",
                    linewidth=3,
                    solid_capstyle="butt",
                )
        elif opening.face == "floor":
            ax.add_patch(
                Rectangle(
                    (x, y),
                    opening.width_mm,
                    opening.height_mm,
                    fill=False,
                    edgecolor="red",
                    linewidth=1.5,
                )
            )
        else:  # lid
            ax.add_patch(
                Rectangle(
                    (x, y),
                    opening.width_mm,
                    opening.height_mm,
                    fill=False,
                    edgecolor="purple",
                    linewidth=1.2,
                    linestyle=":",
                )
            )
    for post in layout.posts:
        ax.add_patch(
            plt.Circle(
                (cfg.wall_mm + post.x_mm, cfg.wall_mm + post.y_mm),
                post.od_mm / 2.0,
                fill=False,
                linewidth=1,
            )
        )
    ax.set_title("plan (red = wall/floor cutout, dotted purple = lid)")
    ax.set_aspect("equal")
    ax.set_xlim(-5, layout.outer_x_mm + 5)
    ax.set_ylim(-5, layout.outer_y_mm + 5)


def _draw_elevation(ax: plt.Axes, layout: EnclosureLayout, axis: str) -> None:
    cfg = layout.config
    wall = cfg.wall_mm
    span = layout.inner_x_mm if axis == "x" else layout.inner_y_mm
    total_x = layout.outer_x_mm if axis == "x" else layout.outer_y_mm
    ax.add_patch(Rectangle((0, 0), total_x, layout.outer_z_mm, fill=False, linewidth=2))
    ax.add_patch(
        Rectangle(
            (wall, cfg.floor_mm),
            span,
            layout.inner_z_mm,
            fill=False,
            linestyle="--",
            linewidth=1,
        )
    )
    for part in layout.parts:
        lo = wall + (part.x_mm if axis == "x" else part.y_mm)
        width = part.size_x_mm if axis == "x" else part.size_y_mm
        ax.add_patch(
            Rectangle(
                (lo, cfg.floor_mm + part.z_mm),
                width,
                part.size_z_mm,
                alpha=0.55,
                facecolor=_MOUNT_COLOR.get(part.mount.value, "gray"),
                edgecolor="black",
                linewidth=0.5,
            )
        )
    for post in layout.posts:
        px = wall + (post.x_mm if axis == "x" else post.y_mm)
        ax.plot(
            [px, px],
            [cfg.floor_mm, cfg.floor_mm + layout.inner_z_mm],
            color="dimgray",
        )
    ax.set_title(f"elevation along {axis} (blue=base, orange=wall, green=lid)")
    ax.set_xlim(-5, total_x + 5)
    ax.set_ylim(-2, layout.outer_z_mm + 5)
    ax.set_aspect("equal")


def render(layout: EnclosureLayout, out_png: Path) -> Path:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    _draw_plan(axes[0], layout)
    _draw_elevation(axes[1], layout, "x")
    _draw_elevation(axes[2], layout, "y")
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Render a Phase 4 enclosure layout to PNG.")
    parser.add_argument("spec", help="BuildSpec JSON file")
    parser.add_argument("out", nargs="?", default="layout.png", help="output PNG")
    parser.add_argument(
        "--export-dir", default=None, help="also export base/lid/assembled STL here"
    )
    args = parser.parse_args(argv[1:])

    spec_path = Path(args.spec)
    if not spec_path.is_file():
        print(f"no such file: {spec_path}", file=sys.stderr)
        return 2
    spec = parse_build_spec(json.loads(spec_path.read_text()))

    result = generate_enclosure(spec, get_catalog(), out_dir=args.export_dir)
    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.code}: {issue.message}")
    if result.layout is None:
        print("layout failed; nothing to render", file=sys.stderr)
        return 1

    print(f"wrote {render(result.layout, Path(args.out))}")
    for stl in result.exported:
        print(f"wrote {stl}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
