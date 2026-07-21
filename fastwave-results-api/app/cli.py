"""python -m app.cli <publish-meet|unpublish-meet|list-meets>

Dev utility for the publishing workflow ahead of Step 5's real upload/review/
publish endpoints. publish_meet/unpublish_meet are plain async functions
(not just argparse handlers) so tests can call them directly to seed a
published meet without going through a subprocess.
"""

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import Meet


async def publish_meet(meet_id: str) -> None:
    async with AsyncSessionLocal() as session:
        meet = await session.get(Meet, meet_id)
        if meet is None:
            print(f"No meet with id {meet_id}")
            return
        meet.publishedAt = datetime.now(timezone.utc)
        await session.commit()
        print(f'Published "{meet.name}" ({meet.id}) at {meet.publishedAt.isoformat()}')


async def unpublish_meet(meet_id: str) -> None:
    async with AsyncSessionLocal() as session:
        meet = await session.get(Meet, meet_id)
        if meet is None:
            print(f"No meet with id {meet_id}")
            return
        meet.publishedAt = None
        await session.commit()
        print(f'Unpublished "{meet.name}" ({meet.id})')


async def list_meets() -> None:
    async with AsyncSessionLocal() as session:
        meets = (await session.execute(select(Meet).order_by(Meet.startDate.desc()))).scalars().all()
        if not meets:
            print("No meets.")
            return
        for m in meets:
            state = f"published at {m.publishedAt.isoformat()}" if m.publishedAt else "draft"
            print(f"{m.id}  {m.name}  [{state}]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fastwave admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish_parser = subparsers.add_parser("publish-meet", help="Set a meet's publishedAt to now")
    publish_parser.add_argument("meet_id")

    unpublish_parser = subparsers.add_parser("unpublish-meet", help="Clear a meet's publishedAt")
    unpublish_parser.add_argument("meet_id")

    subparsers.add_parser("list-meets", help="List meets with id, name, and published state")

    args = parser.parse_args()

    if args.command == "publish-meet":
        asyncio.run(publish_meet(args.meet_id))
    elif args.command == "unpublish-meet":
        asyncio.run(unpublish_meet(args.meet_id))
    elif args.command == "list-meets":
        asyncio.run(list_meets())


if __name__ == "__main__":
    main()
