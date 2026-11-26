"""Typed Parquet tables ("silver"), written atomically with provenance metadata.

A file is validated against its schema, written to a temporary name and renamed into
place, so a reader never sees half a file. Each file records which snapshot it was
built from and which schema version it follows.
"""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from computers import __version__
from computers.data import schemas

__all__ = ["META_DIGEST", "META_SCHEMA_VERSION", "META_WRITER", "read_metadata", "read_table", "silver_path", "write_table"]

META_DIGEST = b"computers.snapshot_digest"
META_SCHEMA_VERSION = b"computers.schema_version"
META_WRITER = b"computers.written_by"


def silver_path(data_dir: str | os.PathLike[str], table_name: str, tile: str) -> Path:
    if table_name not in schemas.SCHEMAS:
        raise ValueError(f"unknown table {table_name!r}")
    return Path(data_dir) / "silver" / table_name / f"tile={tile}.parquet"


def write_table(
    table_name: str,
    table: pa.Table,
    path: str | os.PathLike[str],
    *,
    snapshot_digest: str,
) -> Path:
    """Validate and write `table`. Raises `SchemaError` before touching the disk."""
    schemas.validate(table_name, table)
    spec = schemas.SCHEMAS[table_name]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(table.schema.metadata or {})
    meta.update(
        {
            META_DIGEST: snapshot_digest.encode(),
            META_SCHEMA_VERSION: str(spec.version).encode(),
            META_WRITER: f"computers/{__version__}".encode(),
        }
    )
    ordered = table.select(spec.schema.names).cast(spec.schema).replace_schema_metadata(meta)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        pq.write_table(ordered, tmp, compression="zstd")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def read_table(path: str | os.PathLike[str]) -> pa.Table:
    return pq.read_table(path)


def read_metadata(path: str | os.PathLike[str]) -> dict[str, str]:
    """The `computers.*` metadata of a file as plain strings."""
    meta = pq.read_schema(path).metadata or {}
    return {k.decode(): v.decode() for k, v in meta.items() if k.startswith(b"computers.")}
