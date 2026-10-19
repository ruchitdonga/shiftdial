"""Which output formats and tools apply to which file types."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CAMERA_RAW = "cr2 cr3 nef nrw arw srf sr2 dng orf rw2 raf pef srw raw rwl 3fr erf kdc mrw x3f"
KIND_EXTS: dict[str, frozenset[str]] = {
    "image": frozenset(("png jpg jpeg jfif webp avif ico bmp dib gif tif tiff heic heif tga ppm pgm pbm pnm "
                        "pcx dds jp2 j2k jpf icns qoi psd sgi xbm " + CAMERA_RAW).split()),
    "vector": frozenset("svg svgz emf wmf".split()),
    "video": frozenset("mp4 mov mkv webm avi m4v wmv flv mpg mpeg 3gp 3g2 ts m2ts mts ogv vob".split()),
    "audio": frozenset("mp3 wav flac ogg oga opus m4a aac wma aif aiff amr ape wv tta dsf dff dts ac3 "
                       "eac3 mka caf spx".split()),
    "subtitle": frozenset("srt ass ssa vtt".split()),
    "pdf": frozenset({"pdf"}),
    "document": frozenset("doc docx docm dot dotx odt fodt ott rtf wps wpd".split()),
    "spreadsheet": frozenset("xls xlsx xlsm xlt xltx ods fods csv tsv".split()),
    "slides": frozenset("ppt pptx pptm pot potx pps ppsx odp otp fodp".split()),
    "text": frozenset("txt md markdown htm html".split()),
    "ebook": frozenset("epub mobi azw azw3 fb2 cbz xps oxps".split()),
    "data": frozenset("json jsonl ndjson yaml yml toml xml".split()),
    "font": frozenset("ttf otf woff woff2".split()),
    "model": frozenset("glb gltf stl obj ply 3mf off".split()),
    "cad": frozenset({"dxf"}),
    "archive": frozenset("zip 7z rar tar tgz tbz2 txz tzst iso cab".split()),
}
COMPOUND_ARCHIVES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")

FORMATS: dict[str, list[str]] = {
    "image": ["png", "jpg", "webp", "avif", "tiff", "gif", "bmp", "ico", "pdf", "svg"],
    "vector": ["pdf", "png", "jpg"],
    "video": ["mp4", "hevc", "av1", "webm", "mkv", "mov", "avi", "gif", "apng", "mp3"],
    "audio": ["mp3", "m4a", "aac", "opus", "ogg", "flac", "wav", "aiff", "alac", "wma"],
    "subtitle": ["srt", "ass", "vtt"],
    "pdf": ["png", "jpg", "txt", "docx", "svg", "cbz"],
    "document": ["pdf", "docx", "odt", "rtf", "txt", "html"],
    "spreadsheet": ["pdf", "xlsx", "ods", "csv", "tsv", "html", "json"],
    "slides": ["pdf", "png", "jpg", "pptx", "ppt", "odp"],
    "text": ["pdf", "docx", "html", "md", "txt"],
    "ebook": ["pdf", "txt"],
    "data": ["json", "yaml", "toml", "xml", "csv", "xlsx", "jsonl"],
    "font": ["ttf", "woff", "woff2"],
    "model": ["glb", "stl", "obj", "ply", "off"],
    "cad": ["pdf", "svg", "png"],
}
ARCHIVES: list[str] = ["zip", "7z", "tar.gz"]


def kind_of(path: Path) -> str:
    if path.is_dir():
        return "folder"
    if path.name.lower().endswith(COMPOUND_ARCHIVES):
        return "archive"
    ext = path.suffix.lower().lstrip(".")
    return next((k for k, exts in KIND_EXTS.items() if ext in exts), "other")


def kind_of_all(paths: list[Path]) -> str:
    """The shared kind of every path, or 'mixed'."""
    kinds = {kind_of(p) for p in paths}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def formats_for(paths: list[Path]) -> list[str]:
    """Formats valid for every dragged file. Packing is always offered: all three archive"""
    kind = kind_of_all(paths)
    if kind == "archive":
        return ["archive:extract", *ARCHIVES]
    own = FORMATS.get(kind, [])
    return own + (ARCHIVES if len(own) <= 7 else ["zip"])


@dataclass(frozen=True)
class Tool:
    id: str  # "<kind>:<name>"; the engine function is engines.<module>.tool_<name>
    label: str
    window: bool
    min_files: int = 1

    @property
    def kind(self) -> str:
        return self.id.split(":")[0]


TOOLS: list[Tool] = [
    Tool("image:resize", "Resize", True),
    Tool("image:compress", "Compress", True),
    Tool("image:rotate", "Rotate", True),
    Tool("image:crop", "Crop", True),
    Tool("image:grayscale", "Grayscale", False),
    Tool("image:strip_exif", "Strip EXIF", False),
    Tool("image:watermark", "Watermark", True),
    Tool("video:compress", "Compress", True),
    Tool("video:trim", "Trim", True),
    Tool("video:speed", "Speed", True),
    Tool("video:scale", "Resize", True),
    Tool("video:mute", "Mute", False),
    Tool("video:frame", "Frame", True),
    Tool("video:crop", "Crop", True),
    Tool("video:subs", "Subtitles", False),
    Tool("audio:trim", "Trim", True),
    Tool("audio:normalize", "Normalize", False),
    Tool("audio:mono", "Mono", False),
    Tool("audio:stereo", "Stereo", False),
    Tool("pdf:merge", "Merge", False, min_files=2),
    Tool("pdf:split", "Split", False),
    Tool("pdf:rotate", "Rotate", True),
    Tool("pdf:delete", "Delete pages", True),
    Tool("pdf:compress", "Compress", False),
    Tool("archive:extract", "Extract", False),
    Tool("folder:zip", "Zip", False),
    Tool("folder:7z", "7Z", False),
    Tool("folder:targz", "TAR.GZ", False),
]
TOOL_BY_ID = {t.id: t for t in TOOLS}


def tools_for(paths: list[Path]) -> list[str]:
    """Tool ids valid for every dragged file."""
    kind = kind_of_all(paths)
    return [t.id for t in TOOLS if t.kind == kind and len(paths) >= t.min_files]


def label_for(item: str) -> str:
    """Wheel/card text for a format ('webp' -> 'WEBP') or tool id ('video:trim' -> 'Trim')."""
    return TOOL_BY_ID[item].label if item in TOOL_BY_ID else item.upper()
