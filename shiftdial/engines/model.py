"""3D models: GLB/glTF, STL, OBJ, PLY, OFF, 3MF → GLB, STL, OBJ, PLY, OFF (trimesh)."""
from __future__ import annotations

from pathlib import Path

import trimesh

from shiftdial.engines import JobContext
from shiftdial.utils.paths import Output


def convert(paths: list[Path], fmt: str, ctx: JobContext) -> list[Path]:
    outputs: list[Path] = []
    for i, src in enumerate(paths):
        ctx.check()
        ctx.progress(i / len(paths), f"{src.name} → {fmt.upper()}")
        # shortcut: scenes are merged into one mesh, so textures and materials are dropped;
        # export the trimesh Scene instead if those ever matter.
        mesh = trimesh.load(str(src), force="mesh")
        if mesh.is_empty:
            raise ValueError(f"{src.name} has no geometry")
        with Output.next_to(src, fmt) as out:
            mesh.export(str(out.tmp), file_type=fmt)
        outputs.append(out.final)
    ctx.progress(1.0, "Done")
    return outputs
