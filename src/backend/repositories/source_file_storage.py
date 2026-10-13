"""企业导入源文件的受控定位与不可变副本存储。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from fact_layer.models import SourceKind


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRIVATE_SOURCE_ROOT = PROJECT_ROOT / "data" / "facts" / "source_files"
COPY_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class PreparedSourceFile:
    original_filename: str
    content_sha256: str
    byte_size: int
    source_kind: SourceKind
    read_path: Path
    storage_key: str | None = None
    source_uri: str | None = None
    immutable_copy: bool = False


def hash_source_file(file_path: str | Path) -> tuple[str, int]:
    """以固定大小分块计算 SHA-256，不把整个文件读入内存。"""
    digest = hashlib.sha256()
    byte_size = 0
    with Path(file_path).open("rb") as source:
        while chunk := source.read(COPY_CHUNK_SIZE):
            digest.update(chunk)
            byte_size += len(chunk)
    return digest.hexdigest(), byte_size


def _controlled_local_uri(file_path: Path) -> str:
    resolved = file_path.resolve()
    try:
        relative = resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return resolved.as_uri()
    return f"project://{relative.as_posix()}"


class ImmutableSourceFileStorage:
    """使用 SHA-256 内容寻址保存上传文件，已有对象永不覆盖。"""

    def __init__(self, root: str | Path = DEFAULT_PRIVATE_SOURCE_ROOT) -> None:
        self.root = Path(root)

    def persist(self, source_path: str | Path) -> PreparedSourceFile:
        source = Path(source_path)
        digest, byte_size = hash_source_file(source)
        suffix = source.suffix.lower()
        storage_key = f"sha256/{digest[:2]}/{digest}{suffix}"
        destination = self.root / Path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            existing_digest, existing_size = hash_source_file(destination)
            if existing_digest != digest or existing_size != byte_size:
                raise RuntimeError("不可变源文件存储发生哈希冲突")
        else:
            try:
                with source.open("rb") as input_stream, destination.open("xb") as output_stream:
                    while chunk := input_stream.read(COPY_CHUNK_SIZE):
                        output_stream.write(chunk)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
            except FileExistsError:
                existing_digest, existing_size = hash_source_file(destination)
                if existing_digest != digest or existing_size != byte_size:
                    raise RuntimeError("不可变源文件存储发生并发写入冲突")
            except Exception:
                if destination.exists():
                    try:
                        destination.unlink()
                    except OSError:
                        pass
                raise

            copied_digest, copied_size = hash_source_file(destination)
            if copied_digest != digest or copied_size != byte_size:
                try:
                    destination.unlink()
                except OSError:
                    pass
                raise RuntimeError("上传副本与源文件哈希不一致")

        return PreparedSourceFile(
            original_filename=source.name,
            content_sha256=digest,
            byte_size=byte_size,
            source_kind=SourceKind.UPLOAD,
            read_path=destination,
            storage_key=storage_key,
            immutable_copy=True,
        )


def prepare_source_file(
    file_path: str | Path,
    source_kind: SourceKind | str = SourceKind.LOCAL,
    *,
    source_uri: str | None = None,
    immutable_storage: ImmutableSourceFileStorage | None = None,
) -> PreparedSourceFile:
    """准备上传、本地或 ERP 来源；除上传外只保存受控引用与哈希。"""
    source = Path(file_path)
    if not source.is_file():
        raise FileNotFoundError(f"导入源文件不存在：{source.name}")
    kind = SourceKind(source_kind)
    if kind == SourceKind.UPLOAD:
        return (immutable_storage or ImmutableSourceFileStorage()).persist(source)

    digest, byte_size = hash_source_file(source)
    if kind == SourceKind.ERP and not source_uri:
        raise ValueError("ERP 来源必须提供受控 source_uri")
    controlled_uri = source_uri or _controlled_local_uri(source)
    return PreparedSourceFile(
        original_filename=source.name,
        content_sha256=digest,
        byte_size=byte_size,
        source_kind=kind,
        read_path=source,
        source_uri=controlled_uri,
    )


__all__ = [
    "DEFAULT_PRIVATE_SOURCE_ROOT",
    "ImmutableSourceFileStorage",
    "PreparedSourceFile",
    "hash_source_file",
    "prepare_source_file",
]
