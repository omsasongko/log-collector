
import hashlib
import logging
import os
import re
import time

import pyodbc


# ============================================================
# CONFIGURATION
# ============================================================

SOURCE = (
    r"\\WKS469-15ID\Users\sa.market04id"
    r"\AppData\Roaming\PCS\CMSMarketinglabel.log"
)

SERVER = r"WKS469-3ID\SQLEXPRESS"
DATABASE = "LOG_COLLECTOR_DB"

BATCH_SIZE = 1000
SYNC_INTERVAL = 60
RETRY_INTERVAL = 15

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ============================================================
# LOG PARSER
# Format:
# 2025-10-04 18.44.02.820 - 37370 - DGLII
# ============================================================

PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) "
    r"(\d{2})\.(\d{2})\.(\d{2})\.(\d{3})"
    r"\s+-\s+(\d+)\s+-\s+(\S+)$"
)


def parse_line(raw):
    match = PATTERN.match(raw.strip())

    if not match:
        return None, None, None

    date, hh, mm, ss, ms, number, code = match.groups()

    timestamp = f"{date} {hh}:{mm}:{ss}.{ms}"

    return timestamp, number, code


# ============================================================
# SQL SERVER CONNECTION
# ============================================================

def connect():
    connection_string = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
        "Connection Timeout=15;"
    )

    return pyodbc.connect(
        connection_string,
        autocommit=False,
        timeout=15
    )


# ============================================================
# CHECKPOINT
# ============================================================

def read_checkpoint(cursor):
    cursor.execute("""
        SELECT ByteOffset, HeadHash
        FROM dbo.CollectorCheckpoint
        WHERE SourceFile = ?
    """, SOURCE)

    return cursor.fetchone()


def save_batch(cursor, records, next_offset, head_hash):
    """
    Insert log entries and update checkpoint
    in the same SQL transaction.
    """

    for offset, raw in records:
        timestamp, number, code = parse_line(raw)

        cursor.execute("""
            IF NOT EXISTS (
                SELECT 1
                FROM dbo.LogEntries WITH (UPDLOCK, HOLDLOCK)
                WHERE SourceFile = ?
                  AND ByteOffset = ?
            )
            BEGIN
                INSERT INTO dbo.LogEntries (
                    SourceFile,
                    ByteOffset,
                    LogDateTime,
                    LogNumber,
                    LogCode,
                    RawLog
                )
                VALUES (?, ?, ?, ?, ?, ?)
            END
        """,
            SOURCE,
            offset,
            SOURCE,
            offset,
            timestamp,
            number,
            code,
            raw
        )

    cursor.execute("""
        UPDATE dbo.CollectorCheckpoint
        SET
            ByteOffset = ?,
            HeadHash = ?,
            UpdatedAt = SYSDATETIME()
        WHERE SourceFile = ?;

        IF @@ROWCOUNT = 0
        BEGIN
            INSERT INTO dbo.CollectorCheckpoint (
                SourceFile,
                ByteOffset,
                HeadHash
            )
            VALUES (?, ?, ?);
        END
    """,
        next_offset,
        head_hash,
        SOURCE,
        SOURCE,
        next_offset,
        head_hash
    )


# ============================================================
# SYNCHRONIZATION
# ============================================================

def synchronize():
    connection = None

    try:
        logging.info("Connecting to SQL Server...")

        connection = connect()
        cursor = connection.cursor()

        logging.info("SQL Server connected")

        logging.info("Opening source file...")

        with open(SOURCE, "rb") as source:

            source_size = os.fstat(
                source.fileno()
            ).st_size

            # Identify beginning of source file
            head_hash = hashlib.sha256(
                source.read(4096)
            ).hexdigest()

            checkpoint = read_checkpoint(cursor)

            if checkpoint:

                offset = int(checkpoint[0])
                old_hash = checkpoint[1].strip()

                logging.info(
                    "Resuming from checkpoint: byte offset %s",
                    offset
                )

                if source_size < offset:
                    raise RuntimeError(
                        "Source file smaller than checkpoint. "
                        "File may have been reset or replaced."
                    )

                if old_hash != head_hash:
                    raise RuntimeError(
                        "Source file header changed. "
                        "Manual review required."
                    )

            else:
                offset = 0

                logging.info(
                    "No checkpoint found. "
                    "Starting initial import."
                )

            source.seek(offset)

            batch = []
            total = 0

            while True:

                line_offset = source.tell()
                line_bytes = source.readline()

                if not line_bytes:
                    break

                # Do not commit incomplete lines
                if not line_bytes.endswith(b"\n"):
                    logging.info(
                        "Incomplete trailing line detected. "
                        "Waiting for next sync."
                    )
                    break

                raw = line_bytes.decode(
                    "utf-8",
                    errors="replace"
                ).rstrip("\r\n")

                batch.append((line_offset, raw))

                if len(batch) >= BATCH_SIZE:

                    next_offset = source.tell()

                    save_batch(
                        cursor,
                        batch,
                        next_offset,
                        head_hash
                    )

                    connection.commit()

                    total += len(batch)

                    logging.info(
                        "Committed %s lines",
                        total
                    )

                    batch.clear()

            # Commit remaining complete lines
            if batch:

                next_offset = source.tell()

                save_batch(
                    cursor,
                    batch,
                    next_offset,
                    head_hash
                )

                connection.commit()

                total += len(batch)

                logging.info(
                    "Committed final batch: %s lines",
                    total
                )

            logging.info(
                "Sync completed | "
                "Processed=%s | "
                "Source size=%s bytes",
                total,
                source_size
            )

        return True

    except Exception:

        logging.exception(
            "Synchronization failed. "
            "Automatic retry will be performed."
        )

        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                logging.warning(
                    "Rollback failed. "
                    "SQL connection may have been lost."
                )

        return False

    finally:

        if connection is not None:
            try:
                connection.close()
            except Exception:
                logging.warning(
                    "Could not close SQL connection cleanly."
                )


# ============================================================
# MAIN LOOP
# ============================================================

if __name__ == "__main__":

    logging.info("=" * 55)
    logging.info("PYTHON LOG COLLECTOR STARTED")
    logging.info("Source : %s", SOURCE)
    logging.info("SQL    : %s", SERVER)
    logging.info("DB     : %s", DATABASE)
    logging.info("Batch  : %s", BATCH_SIZE)
    logging.info("Sync   : %s seconds", SYNC_INTERVAL)
    logging.info("Retry  : %s seconds", RETRY_INTERVAL)
    logging.info("=" * 55)

    try:

        while True:

            success = synchronize()

            if success:

                logging.info(
                    "Synchronization successful. "
                    "Next check in %s seconds.",
                    SYNC_INTERVAL
                )

                time.sleep(SYNC_INTERVAL)

            else:

                logging.warning(
                    "Source or SQL connection unavailable. "
                    "Retrying in %s seconds...",
                    RETRY_INTERVAL
                )

                time.sleep(RETRY_INTERVAL)

    except KeyboardInterrupt:

        logging.info(
            "Collector stopped manually by user."
        )
