"""python -m app.ingestion.cli <file.hy3> --uploaded-by <email>"""

import argparse
import asyncio
import json
import sys

from app.db import AsyncSessionLocal
from app.ingestion.service import ingest_file


async def _run(path: str, uploaded_by: str) -> int:
    async with AsyncSessionLocal() as session:
        result = await ingest_file(path, uploaded_by, session)

    print(json.dumps({"uploadId": result.upload_id, "status": result.status, "duplicate": result.duplicate}, indent=2))
    print(json.dumps(result.report, indent=2))

    return 0 if result.status in ("promoted", "needs_review") or result.duplicate else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a Hy-Tek HY3 meet results file.")
    parser.add_argument("file", help="Path to the .hy3 file to ingest.")
    parser.add_argument("--uploaded-by", required=True, help="Email of the uploading user.")
    args = parser.parse_args()

    exit_code = asyncio.run(_run(args.file, args.uploaded_by))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
