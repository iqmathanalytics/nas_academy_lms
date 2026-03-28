"""Import courses/modules/content/challenges from SOURCE_DATABASE_URL into DATABASE_URL. See backend/.env.example."""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR / ".env")

# Models need Base from database (DATABASE_URL must be set and valid for import).
from models import ContentItem, Course, CourseChallenge, Module, User


def _normalize_async_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("mysql+aiomysql://"):
        return url
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+aiomysql://", 1)
    return url


def _strip_mysql_ssl_query(url: str) -> str:
    if not url.startswith("mysql+aiomysql://"):
        return url
    parts = urlsplit(url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in query_pairs if k not in {"ssl_verify_cert", "ssl_verify_identity", "ssl_ca"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(filtered), parts.fragment))


def _ssl_context_from_ca_path(ca_path: str | None) -> ssl.SSLContext:
    if ca_path and ca_path.strip():
        return ssl.create_default_context(cafile=ca_path.strip())
    return ssl.create_default_context()


def _mysql_connect_args(ca_path: str | None) -> dict:
    return {"ssl": _ssl_context_from_ca_path(ca_path)}


def _ca_path_for_source() -> str | None:
    return os.getenv("SOURCE_DB_SSL_CA_PATH") or os.getenv("DB_SSL_CA_PATH")


def _ca_path_for_destination() -> str | None:
    return os.getenv("DESTINATION_DB_SSL_CA_PATH") or os.getenv("DB_SSL_CA_PATH")


def build_mysql_async_engine(url: str, *, ca_path: str | None) -> object:
    au = _strip_mysql_ssl_query(_normalize_async_url(url.strip()))
    if not au.startswith("mysql+aiomysql://"):
        raise SystemExit("This import tool expects mysql:// URLs for TiDB/MySQL source and destination.")
    return create_async_engine(
        au,
        echo=False,
        connect_args=_mysql_connect_args(ca_path),
        pool_pre_ping=True,
    )


def _urls_point_to_same_cluster(a: str, b: str) -> bool:
    """Same host + database path => same cluster DB for migration purposes."""
    try:
        pa, pb = urlsplit(a.strip()), urlsplit(b.strip())
        return (pa.netloc, pa.path.rstrip("/")) == (pb.netloc, pb.path.rstrip("/"))
    except Exception:
        return a.strip() == b.strip()


def resolve_destination_url(cli_dest: str | None) -> str:
    """Writes go here. CLI wins, then IMPORT_DESTINATION_DATABASE_URL, then DATABASE_URL."""
    if cli_dest and cli_dest.strip():
        return cli_dest.strip()
    env_dest = os.getenv("IMPORT_DESTINATION_DATABASE_URL", "").strip()
    if env_dest:
        return env_dest
    db = os.getenv("DATABASE_URL", "").strip()
    if not db:
        raise SystemExit("Set DATABASE_URL (destination) or IMPORT_DESTINATION_DATABASE_URL in backend/.env")
    return db


async def _ensure_instructor(session: AsyncSession, instructor_id: int) -> None:
    res = await session.execute(select(User).where(User.id == instructor_id))
    u = res.scalars().first()
    if not u:
        raise SystemExit(f"No user with id={instructor_id} in the destination database.")
    if (u.role or "").lower() != "instructor":
        raise SystemExit(f"User id={instructor_id} must have role 'instructor' (got {u.role!r}).")


async def _existing_titles_for_instructor(session: AsyncSession, instructor_id: int) -> set[str]:
    res = await session.execute(select(Course.title).where(Course.instructor_id == instructor_id))
    return {((t or "").strip().lower()) for t in res.scalars().all()}


async def run_import(
    *,
    dry_run: bool,
    only_published: bool,
    skip_existing_titles: bool,
    destination_database_url: str | None,
    allow_same_cluster: bool,
) -> None:
    source_url = os.getenv("SOURCE_DATABASE_URL", "").strip()
    if not source_url:
        raise SystemExit("Set SOURCE_DATABASE_URL in backend/.env (read / iqmath cluster).")

    dest_url = resolve_destination_url(destination_database_url)

    if not allow_same_cluster and _urls_point_to_same_cluster(source_url, dest_url):
        raise SystemExit(
            "SOURCE_DATABASE_URL and the destination URL resolve to the same host/database.\n"
            "Import reads from SOURCE and writes to the destination URL (DATABASE_URL or "
            "IMPORT_DESTINATION_DATABASE_URL).\n"
            "Paste your NAS destination TiDB connection string into DATABASE_URL (or "
            "IMPORT_DESTINATION_DATABASE_URL), and keep iqmath on SOURCE_DATABASE_URL only.\n"
            "If you really mean to copy inside one DB, pass --allow-same-cluster (duplicates rows)."
        )

    instructor_s = os.getenv("IMPORT_INSTRUCTOR_ID", "").strip()
    if not instructor_s.isdigit():
        raise SystemExit("Set IMPORT_INSTRUCTOR_ID to a numeric user id that exists on the destination DB.")
    instructor_id = int(instructor_s)

    source_engine = build_mysql_async_engine(source_url, ca_path=_ca_path_for_source())
    target_engine = build_mysql_async_engine(dest_url, ca_path=_ca_path_for_destination())

    SourceSession = async_sessionmaker(bind=source_engine, class_=AsyncSession, expire_on_commit=False)
    TargetSession = async_sessionmaker(bind=target_engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with SourceSession() as src, TargetSession() as tgt:
            await _ensure_instructor(tgt, instructor_id)
            existing_titles = await _existing_titles_for_instructor(tgt, instructor_id)

            q = select(Course)
            if only_published:
                q = q.where(Course.is_published.is_(True))
            courses = (await src.execute(q)).scalars().all()

            if not courses:
                print("No courses found in source (check SOURCE_DATABASE_URL and filters).")
                return

            print(f"[source] courses: {len(courses)}")
            _du = urlsplit(dest_url)
            _host = _du.netloc.split("@")[-1] if "@" in _du.netloc else _du.netloc
            print(f"[destination] writing to: {_host}{_du.path}")
            for c in courses:
                flag = (
                    " [skip: title exists on dest]"
                    if skip_existing_titles and (c.title or "").strip().lower() in existing_titles
                    else ""
                )
                print(f"  - id={c.id} title={c.title!r}{flag}")

            if dry_run:
                print("Dry run: no rows written.")
                return

            course_id_map: dict[int, int] = {}
            module_id_map: dict[int, int] = {}
            item_count = 0
            chal_count = 0

            for c in courses:
                key = (c.title or "").strip().lower()
                if skip_existing_titles and key in existing_titles:
                    print(f"Skipping existing title: {c.title!r}")
                    continue

                new_course = Course(
                    title=c.title,
                    description=c.description,
                    price=c.price,
                    image_url=c.image_url,
                    is_published=c.is_published,
                    instructor_id=instructor_id,
                    course_type=c.course_type,
                    language=c.language,
                )
                tgt.add(new_course)
                await tgt.flush()
                course_id_map[c.id] = new_course.id
                existing_titles.add(key)

            if not course_id_map:
                print("Nothing to import after filters.")
                await tgt.rollback()
                return

            old_course_ids = list(course_id_map.keys())

            mods = (
                await src.execute(select(Module).where(Module.course_id.in_(old_course_ids)))
            ).scalars().all()
            for m in mods:
                nc = course_id_map.get(m.course_id)
                if nc is None:
                    continue
                nm = Module(title=m.title, order=m.order, course_id=nc)
                tgt.add(nm)
                await tgt.flush()
                module_id_map[m.id] = nm.id

            if module_id_map:
                old_mod_ids = list(module_id_map.keys())
                items = (
                    await src.execute(select(ContentItem).where(ContentItem.module_id.in_(old_mod_ids)))
                ).scalars().all()
                for it in items:
                    mid = module_id_map.get(it.module_id)
                    if mid is None:
                        continue
                    ni = ContentItem(
                        title=it.title,
                        type=it.type,
                        content=it.content,
                        duration=it.duration,
                        is_mandatory=it.is_mandatory,
                        order=it.order,
                        module_id=mid,
                        instructions=it.instructions,
                        test_config=it.test_config,
                        start_time=it.start_time,
                        end_time=it.end_time,
                    )
                    tgt.add(ni)
                    item_count += 1

            chals = (
                await src.execute(select(CourseChallenge).where(CourseChallenge.course_id.in_(old_course_ids)))
            ).scalars().all()
            for ch in chals:
                ncid = course_id_map.get(ch.course_id)
                if ncid is None:
                    continue
                tgt.add(
                    CourseChallenge(
                        course_id=ncid,
                        title=ch.title,
                        description=ch.description,
                        difficulty=ch.difficulty,
                        test_cases=ch.test_cases,
                        function_name=ch.function_name,
                    )
                )
                chal_count += 1

            await tgt.commit()
            print(
                f"Done. Imported into destination: {len(course_id_map)} course(s), "
                f"{len(module_id_map)} module(s), {item_count} content item(s), {chal_count} challenge(s)."
            )
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    p = argparse.ArgumentParser(description="Import courses from SOURCE_DATABASE_URL into destination DB.")
    p.add_argument("--dry-run", action="store_true", help="List source courses only; no writes.")
    p.add_argument(
        "--only-published",
        action="store_true",
        help="Import only courses where is_published is true on the source.",
    )
    p.add_argument(
        "--skip-existing-titles",
        action="store_true",
        help="Skip if destination already has the same course title for that instructor.",
    )
    p.add_argument(
        "--destination-database-url",
        default=None,
        help="Override destination URL for this run (otherwise DATABASE_URL or IMPORT_DESTINATION_DATABASE_URL).",
    )
    p.add_argument(
        "--allow-same-cluster",
        action="store_true",
        help="Allow source and destination URLs to point to the same DB (creates duplicates).",
    )
    args = p.parse_args()
    asyncio.run(
        run_import(
            dry_run=args.dry_run,
            only_published=args.only_published,
            skip_existing_titles=args.skip_existing_titles,
            destination_database_url=args.destination_database_url,
            allow_same_cluster=args.allow_same_cluster,
        )
    )


if __name__ == "__main__":
    main()
