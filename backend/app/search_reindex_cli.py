"""Resumable, idempotent admin backfill of finished local/MinIO books.

Run in the worker container: python -m app.search_reindex_cli [--force] [--batch-size 25]
A transaction/row lock per book makes interruption and concurrent edits safe.
"""

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.sql.elements import ColumnElement

from app.config import get_settings
from app.container import build_container
from app.db.models import Job
from app.domain.enums import JobStatus
from app.logging import configure_logging, get_logger
from app.services.library_search import SearchIndexService

log = get_logger("search_reindex")


async def reindex(batch_size: int = 25, force: bool = False) -> tuple[int, int]:
    container = build_container(get_settings())
    done = failed = 0
    last = ""
    try:
        while True:
            async with container.database.session() as session:
                condition: list[ColumnElement[bool]] = [
                    Job.status.in_((JobStatus.SUCCEEDED, JobStatus.PARTIAL)),
                    Job.id > last,
                ]
                if not force:
                    condition.append(Job.search_index_state != "READY")
                ids = (
                    await session.scalars(
                        select(Job.id).where(*condition).order_by(Job.id).limit(batch_size)
                    )
                ).all()
            if not ids:
                break
            for job_id in ids:
                last = job_id
                async with container.database.session() as session:
                    indexer = SearchIndexService(
                        session, container.file_store, container.object_store
                    )
                    try:
                        await indexer.index_job(job_id)
                        done += 1
                    except Exception:
                        # Never emit path, markdown, query, or DB exception parameters.
                        log.warning("search_reindex_failed", job_id=job_id)
                        await session.rollback()
                        try:
                            await indexer.invalidate(job_id)
                        except Exception:
                            log.warning("search_reindex_invalidate_failed", job_id=job_id)
                            await session.rollback()
                        failed += 1
            log.info("search_reindex_progress", indexed=done, failed=failed)
    finally:
        await container.close()
    return done, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Index completed private books for search")
    parser.add_argument("--force", action="store_true", help="Rebuild READY books too")
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error("batch size must be 1 to 100")
    configure_logging(get_settings().log_level)
    indexed, failed = asyncio.run(reindex(args.batch_size, args.force))
    print(f"Indexed: {indexed}; unavailable: {failed}; rerun after correcting storage errors")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
