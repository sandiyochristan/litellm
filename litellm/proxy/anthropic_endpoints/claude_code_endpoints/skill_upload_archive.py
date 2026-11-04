import hashlib
import io
import json
import re
import struct
import zipfile
from dataclasses import dataclass
from typing import Final

from litellm.proxy.discovery_endpoints.agent_skills_archive import build_skill_archive

MAX_SKILL_UPLOAD_BYTES: Final = 10 * 1024 * 1024
MAX_SKILL_ARCHIVE_COMPRESSION_RATIO: Final = 50


@dataclass(frozen=True, slots=True)
class UploadedSkillArchive:
    content: bytes
    digest: str


def build_uploaded_skill_archive(content: bytes, name: str) -> UploadedSkillArchive | None:
    if len(content) > MAX_SKILL_UPLOAD_BYTES or re.fullmatch(r"[a-z0-9-]+", name) is None:
        return None
    try:
        return _build_uploaded_skill_archive(content, name)
    except (struct.error, UnicodeEncodeError):
        return None


def _build_uploaded_skill_archive(content: bytes, name: str) -> UploadedSkillArchive | None:
    skill: Final = build_skill_archive(
        content, ignore_macos_metadata=True, read_frontmatter=False, preserve_executable_bits=True
    )
    if skill is None:
        return None

    manifest: Final = {"name": name}
    with zipfile.ZipFile(io.BytesIO(skill.content)) as files:
        members: Final = ((".claude-plugin/plugin.json", json.dumps(manifest).encode(), 0o644),) + tuple(
            (f"skills/{name}/{entry.filename}", files.read(entry), entry.external_attr >> 16)
            for entry in files.infolist()
        )
    compressed: Final = _pack(members, zipfile.ZIP_DEFLATED)
    result: Final = (
        _pack(members, zipfile.ZIP_STORED)
        if sum(len(data) for _, data, _ in members) > len(compressed) * MAX_SKILL_ARCHIVE_COMPRESSION_RATIO
        else compressed
    )
    return UploadedSkillArchive(content=result, digest=hashlib.sha256(result).hexdigest())


def _plugin_entry(name: str, mode: int) -> zipfile.ZipInfo:
    entry: Final = zipfile.ZipInfo(name)
    entry.create_system = 3
    entry.external_attr = mode << 16
    return entry


def _pack(members: tuple[tuple[str, bytes, int], ...], compression: int) -> bytes:
    buffer: Final = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as plugin:
        for name, data, mode in members:
            plugin.writestr(_plugin_entry(name, mode), data, compress_type=compression)
    return buffer.getvalue()
