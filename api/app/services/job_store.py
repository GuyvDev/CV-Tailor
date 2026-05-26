from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.models import JobMetadata


class JobStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        job_id: str,
        role_focus: str | None,
        template: str,
        extra: dict | None = None,
    ) -> JobMetadata:
        now = datetime.now(UTC)
        metadata = JobMetadata(
            job_id=job_id,
            status="queued",
            created_at=now,
            updated_at=now,
            role_focus=role_focus,
            template=template,
            stage="queued",
            extra=extra or {},
        )
        self.save(metadata)
        return metadata

    def job_dir(self, job_id: str) -> Path:
        path = self.output_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def metadata_path(self, job_id: str) -> Path:
        return self.output_dir / job_id / "metadata.json"

    def save(self, metadata: JobMetadata) -> None:
        metadata.updated_at = datetime.now(UTC)
        (self.job_dir(metadata.job_id) / "metadata.json").write_text(
            metadata.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def load(self, job_id: str) -> JobMetadata:
        return JobMetadata.model_validate_json(
            self.metadata_path(job_id).read_text(encoding="utf-8")
        )

    def delete(self, job_id: str) -> None:
        job_dir = self.output_dir / job_id
        if not job_dir.exists():
            raise FileNotFoundError(job_id)
        shutil.rmtree(job_dir)

    def write_text_artifact(self, job_id: str, filename: str, content: str) -> Path:
        path = self.job_dir(job_id) / filename
        path.write_text(content, encoding="utf-8")
        return path

    def write_binary_artifact(self, job_id: str, filename: str, content: bytes) -> Path:
        path = self.job_dir(job_id) / filename
        path.write_bytes(content)
        return path

    def record_status(self, metadata: JobMetadata, **changes: object) -> JobMetadata:
        updated = metadata.model_copy(update=changes)
        self.save(updated)
        return updated
