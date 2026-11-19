
import hashlib
import logging
import os
import re
import time

import pyodbc

SOURCE = (
    r"\\WKS469-15ID\Users\sa.market04id"
    r"\AppData\Roaming\PCS\CMSMarketinglabel.log"
)

SERVER = r"WKS469-3ID\SQLEXPRESS"
DATABASE = "LOG_COLLECTOR_DB"

BATCH_SIZE = 1000
SYNC_INTERVAL = 60

PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) "
    r"(\d{2})\.(\d{2})\.(\d{2})\.(\d{3})"
    r"\s+-\s+(\d+)\s+-\s+(\S+)$"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

def connect():
    return pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;",
        timeout=15,
        autocommit=False,
    )

def parse_line(raw):
    match = PATTERN.match(raw.strip())

    if not match:
        return None, None, None

    date, hh, mm, ss, ms, number, code = match.groups()
    timestamp = f"{date} {hh}:{mm}:{ss}.{ms}"
    return timestamp, number, code

def read_checkpoint(cursor):
    cursor.execute("""
        SELECT ByteOffset, HeadHash
        FROM dbo.CollectorCheckpoint
        WHERE SourceFile = ?
    """, SOURCE)

    return cursor.fetchone()

def save_batch(cursor, records, next_offset, head_hash):
    # Called inside a SQL transaction.
    for offset, raw in records:
        timestamp, number, code = parse_line(raw)

        cursor.execute("""
            IF NOT EXISTS (
                SELECT 1 FROM dbo.LogEntries
                WHERE SourceFile = ? AND ByteOffset = ?
            )
            INSERT INTO dbo.LogEntries (
                SourceFile, ByteOffset, LogDateTime,
                LogNumber, LogCode, RawLog
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            SOURCE, offset,
            SOURCE, offset, timestamp, number, code, raw
        )

    cursor.execute("""
        UPDATE dbo.CollectorCheckpoint
        SET ByteOffset = ?, HeadHash = ?,
            UpdatedAt = SYSDATETIME()
        WHERE SourceFile = ?

        IF @@ROWCOUNT = 0
            INSERT INTO dbo.CollectorCheckpoint
                (SourceFile, ByteOffset, HeadHash)
            VALUES (?, ?, ?)
    """,
        next_offset, head_hash, SOURCE,
        SOURCE, next_offset, head_hash
    )

def synchronize():
    connection = None

    try:
        connection = connect()
        cursor = connection.cursor()

        with open(SOURCE, "rb") as source:
            source_size = os.fstat(source.fileno()).st_size
            head_hash = hashlib.sha256(
                source.read(4096)
            ).hexdigest()

            checkpoint = read_checkpoint(cursor)

            if checkpoint:
                offset = int(checkpoint[0])
                old_hash = checkpoint[1].strip()

                if source_size < offset:
                    raise RuntimeError(
                        "Source file is smaller than checkpoint"
                    )

                if old_hash != head_hash:
                    raise RuntimeError(
                        "Source file changed: manual review required"
                    )
            else:
                offset = 0

            source.seek(offset)
            batch = []
            total = 0

            while True:
                line_offset = source.tell()
                line_bytes = source.readline()

                if not line_bytes:
                    break

                # Keep incomplete trailing lines for next cycle.
                if not line_bytes.endswith(b"\n"):
                    break

                raw = line_bytes.decode(
                    "utf-8", errors="replace"
                ).rstrip("\r\n")

                batch.append((line_offset, raw))

                if len(batch) >= BATCH_SIZE:
                    next_offset = source.tell()
                    save_batch(
                        cursor, batch, next_offset, head_hash
                    )
                    connection.commit()
                    total += len(batch)
                    logging.info("Committed %s lines", total)
                    batch.clear()

            if batch:
                next_offset = source.tell()
                save_batch(
                    cursor, batch, next_offset, head_hash
                )
                connection.commit()
                total += len(batch)

            logging.info(
                "Sync completed; lines=%s, source_size=%s",
                total, source_size
            )

    except Exception:
        if connection is not None:
            connection.rollback()
        logging.exception("Synchronization failed")
    finally:
        if connection is not None:
            connection.close()

if __name__ == "__main__":
    logging.info("Collector started")

    while True:
        synchronize()
        time.sleep(SYNC_INTERVAL)
