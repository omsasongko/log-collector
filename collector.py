
# ============================================================
# PYTHON LOG COLLECTOR V3 - CATATAN PENGGUNAAN
# ============================================================
# Tujuan:
#   1. Membaca HANYA file .log yang didaftarkan di whitelist.
#   2. Mengambil data dari Windows SMB share (tanpa mengubah file sumber).
#   3. Menyimpan sementara log ke SQLite jika SQL Server belum tersedia.
#   4. Mengirim log ke SQL Server dan menghindari duplikasi berdasarkan
#      filename + generation + byte_offset.
#   5. Mengingat offset baca setelah collector berhenti/restart.
#
# PENTING:
# - Sebelum digunakan, buat tabel dbo.LogEntriesV3 di SQL Server.
# - Hentikan collector versi lama sebelum menjalankan versi ini.
# - Jangan hapus storage/collector_queue.sqlite3: berisi checkpoint
#   dan data pending yang belum terkirim.
# - Deteksi file pengganti berdasarkan metadata/fingerprint tidak
#   menjamin semua pola replace pada SMB terdeteksi sempurna.
# - TrustServerCertificate=yes hanya untuk pengujian; untuk produksi
#   gunakan sertifikat SQL Server yang dipercaya dan divalidasi.
# ============================================================

import hashlib
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

import pyodbc


# ============================================================
# CONFIGURATION
# ============================================================

# Lokasi folder sumber: bukan folder lokal PC collector, melainkan
# folder yang di-share oleh komputer WKS469-15ID.
SOURCE_FOLDER = (
    r"\\WKS469-15ID\Users\sa.market04id"
    r"\AppData\Roaming\PCS"
)

# WHITELIST: HANYA nama file di daftar ini yang dibaca.
# Contoh tambah file: "CMSAlarm.log". Nama yang tidak didaftarkan
# (termasuk nama baru setelah rename) akan diabaikan.
# ONLY these registered files will be monitored.
REGISTERED_LOG_FILES = [
    "CMSMarketinglabel.log",
]

# Target SQL Server dan nama database yang sudah dibuat.
SERVER = r"WKS469-3ID\SQLEXPRESS"
DATABASE = "LOG_COLLECTOR_DB"

# Jeda pemeriksaan setelah siklus normal tanpa pekerjaan: 60 detik.
SYNC_INTERVAL = 60
# Jeda percobaan ulang bila file share atau SQL gagal: 15 detik.
RETRY_INTERVAL = 15

# Sekali transaksi pengiriman SQL maksimal 1.000 record.
BATCH_SIZE = 1000
# Setiap putaran, maksimum 5.000 baris baru dibaca dari file.
READ_LIMIT_PER_CYCLE = 5000
# Batas jumlah record yang diantrekan dalam SQLite.
# Bila penuh, pembacaan berhenti sementara sampai antrean berkurang.
MAX_PENDING = 20000
# Jumlah byte awal file yang di-hash untuk mendeteksi perubahan.
ANCHOR_BYTES = 4096


# ============================================================
# LOCAL STORAGE
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "storage"
DATA_DIR.mkdir(exist_ok=True)

# File SQLite LOKAL: menyimpan checkpoint tiap file + record pending.
# Simpan tetap di disk persisten dan pastikan kapasitasnya cukup.
QUEUE_DB = DATA_DIR / "collector_queue.sqlite3"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            DATA_DIR / "collector.log",
            encoding="utf-8"
        )
    ],
)


# ============================================================
# LOG PARSER
# ============================================================

# Pola contoh: 2025-10-04 18.44.02.820 - 37370 - DGLII
# Jika baris tidak cocok, kolom hasil parsing NULL, tetapi RawLog
# tetap disimpan sehingga data mentah tidak hilang.
PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) "
    r"(\d{2})\.(\d{2})\.(\d{2})\.(\d{3})"
    r"\s+-\s+(\d+)\s+-\s+(\S+)$"
)


def parse_line(raw):
    """Memisahkan satu baris log menjadi timestamp, nomor, dan kode.
    Bila format tidak sesuai, kembalikan None agar RawLog tetap tersimpan."""
    match = PATTERN.fullmatch(raw.strip())

    if not match:
        return None, None, None

    day, hh, mm, ss, ms, number, code = match.groups()

    timestamp = f"{day} {hh}:{mm}:{ss}.{ms}"

    try:
        datetime.strptime(
            timestamp,
            "%Y-%m-%d %H:%M:%S.%f"
        )
    except ValueError:
        return None, None, None

    return timestamp, number[:50], code[:100]


# ============================================================
# SQL SERVER CONNECTION
# ============================================================

def sql_connect():
    """Membuka koneksi SQL Server memakai Windows Authentication.
    Koneksi menggunakan transaksi manual (commit/rollback)."""
    return pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        # Gunakan akun Windows yang menjalankan proses collector.
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        # PERHATIAN: bypass pemeriksaan sertifikat, hanya untuk pilot.
        "TrustServerCertificate=yes;"
        "Connection Timeout=10;",
        timeout=15,
        autocommit=False,
    )


# ============================================================
# SQLITE LOCAL BUFFER
# ============================================================

def init_queue(db):
    """Membuat tabel SQLite untuk posisi baca per file dan antrean kirim.
    Perintah CREATE IF NOT EXISTS menjaga data lokal yang sudah ada."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS file_state (
            filename TEXT PRIMARY KEY,
            generation TEXT NOT NULL,
            next_offset INTEGER NOT NULL,
            anchor_hash TEXT NOT NULL,
            anchor_length INTEGER NOT NULL,
            file_identity TEXT,
            creation_ns INTEGER
        );

        CREATE TABLE IF NOT EXISTS pending (
            filename TEXT NOT NULL,
            generation TEXT NOT NULL,
            byte_offset INTEGER NOT NULL,
            raw_log TEXT NOT NULL,

            PRIMARY KEY (
                filename,
                generation,
                byte_offset
            )
        );

        CREATE INDEX IF NOT EXISTS idx_pending_file
        ON pending(
            filename,
            generation,
            byte_offset
        );
    """)

    db.commit()


def pending_count(db):
    """Menghitung jumlah baris yang belum berhasil dikirim ke SQL Server."""
    return db.execute(
        "SELECT COUNT(*) FROM pending"
    ).fetchone()[0]


# ============================================================
# LEGACY CHECKPOINT MIGRATION
# ============================================================

def sql_legacy_checkpoint(filename):
    """Membaca posisi checkpoint dari collector lama di SQL Server.
    Dipakai sekali ketika file belum memiliki state lokal V3."""
    full_path = os.path.join(
        SOURCE_FOLDER,
        filename
    )

    con = sql_connect()

    try:
        cur = con.cursor()

        cur.execute("""
            SELECT
                ByteOffset,
                HeadHash
            FROM dbo.CollectorCheckpoint
            WHERE SourceFile = ?
        """, full_path)

        row = cur.fetchone()

        if row:
            return int(row[0]), str(row[1]).strip()

        return None

    finally:
        con.close()


# ============================================================
# FILE IDENTITY
# ============================================================

def prefix_fingerprint(handle, length):
    """Menghitung SHA-256 dari beberapa byte pertama file.
    Setelah pengecekan, posisi baca file dikembalikan seperti semula."""
    current = handle.tell()

    try:
        handle.seek(0)

        return hashlib.sha256(
            handle.read(length)
        ).hexdigest()

    finally:
        handle.seek(current)


def metadata(handle):
    """Membaca ukuran serta identitas/metadata file dari Windows/SMB.
    Metadata ini membantu mendeteksi file yang diganti atau dibuat ulang."""
    stat = os.fstat(handle.fileno())

    file_id = (
        str(stat.st_ino)
        if getattr(stat, "st_ino", 0)
        else None
    )

    created = getattr(
        stat,
        "st_birthtime_ns",
        None
    )

    if created is None:
        created = getattr(
            stat,
            "st_ctime_ns",
            None
        )

    return stat.st_size, file_id, created


# ============================================================
# CREATE FILE GENERATION
# ============================================================

def new_state(filename, handle, legacy=None):
    """Membentuk state generasi baru dengan UUID unik.
    Jika checkpoint lama valid, lanjut dari offset lama; jika tidak ada, mulai 0."""
    size, file_id, created = metadata(handle)

    if legacy:
        offset, old_hash = legacy

        # File lebih pendek daripada posisi baca: mungkin truncate/replace.
        if size < offset:
            raise RuntimeError(
                f"{filename}: legacy checkpoint "
                "beyond EOF; manual review needed"
            )

        if prefix_fingerprint(handle, 4096) != old_hash:
            raise RuntimeError(
                f"{filename}: legacy file header differs; "
                "manual review needed"
            )

        length = 4096
        anchor_hash = old_hash

        logging.info(
            "%s: resumed existing SQL checkpoint "
            "at byte %s",
            filename,
            offset
        )

    else:
        offset = 0

        length = min(size, ANCHOR_BYTES)

        anchor_hash = prefix_fingerprint(
            handle,
            length
        )

        logging.info(
            "%s: newly registered file "
            "starts at byte 0",
            filename
        )

    return (
        filename,
        str(uuid.uuid4()),
        offset,
        anchor_hash,
        length,
        file_id,
        created
    )


# ============================================================
# DETECT REPLACED FILE
# ============================================================

def changed_generation(
    state,
    size,
    file_id,
    created,
    handle
):
    """Mengecek apakah file terdaftar tampak sudah diganti.
    Gunakan ukuran, identitas, waktu, dan fingerprint awal."""
    (
        _,
        _,
        offset,
        anchor,
        anchor_len,
        old_id,
        old_created
    ) = state

    if size < offset:
        return True

    # File ID berubah: indikasi file pengganti, jika SMB melaporkannya.
    if old_id and file_id and old_id != file_id:
        return True

    if (
        old_created is not None
        and created is not None
        and old_created != created
    ):
        return True

    # Isi bagian awal berubah: kemungkinan file lama diganti.
    if anchor_len:
        current_hash = prefix_fingerprint(
            handle,
            anchor_len
        )

        if current_hash != anchor:
            return True

    return False


# ============================================================
# FILE CHECKPOINT
# ============================================================

def local_state(db, filename, handle):
    """Mengambil checkpoint lokal dan menentukan apakah perlu generasi baru.
    Perubahan state disimpan permanen di SQLite."""
    row = db.execute("""
        SELECT
            filename,
            generation,
            next_offset,
            anchor_hash,
            anchor_length,
            file_identity,
            creation_ns
        FROM file_state
        WHERE filename = ?
    """, (filename,)).fetchone()

    size, file_id, created = metadata(handle)

    # Registrasi pertama: coba pakai checkpoint SQL lama.
    # Bila tidak ada checkpoint lama, mulai dari byte 0.
    # First registration
    if row is None:
        legacy = sql_legacy_checkpoint(filename)

        state = new_state(
            filename,
            handle,
            legacy
        )

        with db:
            db.execute("""
                INSERT INTO file_state (
                    filename,
                    generation,
                    next_offset,
                    anchor_hash,
                    anchor_length,
                    file_identity,
                    creation_ns
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, state)

        return state

    # Jika file terdaftar dibuat ulang, buat UUID generasi baru.
    # Data generasi sebelumnya di SQL Server tidak dihapus.
    # Replaced or recreated file
    if changed_generation(
        row,
        size,
        file_id,
        created,
        handle
    ):
        logging.warning(
            "%s: new file generation detected. "
            "Old SQL history retained.",
            filename
        )

        state = new_state(
            filename,
            handle
        )

        with db:
            db.execute("""
                UPDATE file_state
                SET
                    generation = ?,
                    next_offset = ?,
                    anchor_hash = ?,
                    anchor_length = ?,
                    file_identity = ?,
                    creation_ns = ?
                WHERE filename = ?
            """, (*state[1:], filename))

        return state

    # Initial empty file starts receiving data
    if row[4] == 0 and size > 0:
        length = min(size, ANCHOR_BYTES)

        anchor = prefix_fingerprint(
            handle,
            length
        )

        with db:
            db.execute("""
                UPDATE file_state
                SET
                    anchor_hash = ?,
                    anchor_length = ?
                WHERE filename = ?
            """, (
                anchor,
                length,
                filename
            ))

        row = (
            row[0],
            row[1],
            row[2],
            anchor,
            length,
            row[5],
            row[6]
        )

    return row


# ============================================================
# READ REGISTERED FILE
# ============================================================

def collect_file(db, filename, allowance):
    """Membaca hanya satu nama file terdaftar dari offset terakhir.
    Baris lengkap dimasukkan ke antrean SQLite bersama update offset."""
    full_path = os.path.join(
        SOURCE_FOLDER,
        filename
    )

    try:
        with open(full_path, "rb") as handle:
            state = local_state(
                db,
                filename,
                handle
            )

            _, generation, offset, *_ = state

            # Lompat langsung ke posisi terakhir yang SUDAH DIANTREKAN.
            # Offset ini tidak harus berarti sudah terkirim ke SQL Server.
            handle.seek(offset)

            records = []

            while len(records) < allowance:
                pos = handle.tell()
                chunk = handle.readline()

                if not chunk:
                    break

                # Hindari mengantre baris yang belum selesai ditulis PCS.
                # Wait for complete line
                if not chunk.endswith(b"\n"):
                    handle.seek(pos)
                    break

                raw = chunk.decode(
                    "utf-8",
                    errors="replace"
                ).rstrip("\r\n")

                records.append((
                    filename,
                    generation,
                    pos,
                    raw
                ))

            next_offset = handle.tell()

            if records:
                # Simpan data pending dan posisi baca dalam SATU transaksi
                # SQLite. Saat restart, data pending tidak akan hilang.
                # Queue and checkpoint are atomic
                with db:
                    db.executemany("""
                        INSERT OR IGNORE INTO pending (
                            filename,
                            generation,
                            byte_offset,
                            raw_log
                        )
                        VALUES (?, ?, ?, ?)
                    """, records)

                    db.execute("""
                        UPDATE file_state
                        SET next_offset = ?
                        WHERE filename = ?
                          AND generation = ?
                    """, (
                        next_offset,
                        filename,
                        generation
                    ))

                logging.info(
                    "%s: queued %s lines, offset=%s",
                    filename,
                    len(records),
                    next_offset
                )

            return len(records), True

    except (
        OSError,
        RuntimeError,
        pyodbc.Error
    ) as error:
        logging.warning(
            "%s: source unavailable or unsafe (%s)",
            filename,
            error
        )

        return 0, False


# ============================================================
# SEND BUFFER TO SQL SERVER
# ============================================================

def flush_queue(db):
    """Mengirim maksimal BATCH_SIZE antrean ke SQL Server.
    Antrean dihapus hanya setelah commit SQL berhasil."""
    rows = db.execute("""
        SELECT
            filename,
            generation,
            byte_offset,
            raw_log
        FROM pending
        ORDER BY rowid
        LIMIT ?
    """, (BATCH_SIZE,)).fetchall()

    if not rows:
        return 0, True

    con = None

    try:
        con = sql_connect()
        cur = con.cursor()

        for filename, generation, offset, raw in rows:
            timestamp, number, code = parse_line(raw)

            cur.execute("""
                IF NOT EXISTS (
                    SELECT 1
                    FROM dbo.LogEntriesV3
                        WITH (UPDLOCK, HOLDLOCK)
                    WHERE SourceFile = ?
                      AND GenerationId = ?
                      AND ByteOffset = ?
                )
                INSERT INTO dbo.LogEntriesV3 (
                    SourceFile,
                    GenerationId,
                    ByteOffset,
                    LogDateTime,
                    LogNumber,
                    LogCode,
                    RawLog
                )
                VALUES (
                    ?,
                    CONVERT(uniqueidentifier, ?),
                    ?, ?, ?, ?, ?
                )
            """,
                filename,
                generation,
                offset,
                filename,
                generation,
                offset,
                timestamp,
                number,
                code,
                raw
            )

        # Data SQL di-commit dulu. Bila koneksi putus saat commit,
        # antrean masih tersedia dan query deduplikasi melindungi retry.
        # Commit SQL first
        con.commit()

        # Hapus antrean lokal setelah SQL dipastikan commit berhasil.
        # Jika proses mati sebelum delete, retry aman berkat kunci unik.
        # Remove from local buffer only after SQL commit
        with db:
            db.executemany("""
                DELETE FROM pending
                WHERE filename = ?
                  AND generation = ?
                  AND byte_offset = ?
            """, [
                (r[0], r[1], r[2])
                for r in rows
            ])

        logging.info(
            "SQL synchronized: %s queued rows",
            len(rows)
        )

        return len(rows), True

    except (
        pyodbc.Error,
        sqlite3.Error
    ) as error:
        logging.warning(
            "SQL sync failed; local queue retained: %s",
            error
        )

        if con:
            try:
                con.rollback()
            except pyodbc.Error:
                pass

        return 0, False

    finally:
        if con:
            try:
                con.close()
            except pyodbc.Error:
                pass


# ============================================================
# STATUS MONITORING TERMINAL
# ============================================================

def display_status(db, collected, sent, pending, source_status,
                   sql_status, next_delay):
    """Menampilkan kesehatan collector berdasarkan siklus terakhir.

    OFFSET berasal dari SQLite file_state dan menunjukkan posisi
    yang sudah dimasukkan ke antrean lokal, BUKAN jaminan seluruh
    data sampai ke SQL Server.
    """
    if pending:
        state = "SYNCING / QUEUED DATA" if sent else "PENDING DATA"
    elif collected or sent:
        state = "SYNC COMPLETED"
    elif source_status == "CONNECTED":
        state = "IDLE - NO NEW DATA"
    elif source_status == "NOT CHECKED (QUEUE FULL)":
        state = "BUFFER FULL"
    else:
        state = "SOURCE UNAVAILABLE"

    logging.info("-----------------------------------------------")
    logging.info("STATUS : %s", state)
    logging.info("SOURCE : %s", source_status)
    logging.info("SQL    : %s", sql_status)

    # Untuk setiap nama file yang diregistrasikan, tampilkan offset.
    for filename in REGISTERED_LOG_FILES:
        row = db.execute(
            "SELECT next_offset FROM file_state WHERE filename = ?",
            (filename,)
        ).fetchone()
        offset = row[0] if row else "NO CHECKPOINT"
        logging.info("OFFSET : %s | %s", offset, filename)

    logging.info("QUEUE  : %s", pending)
    logging.info("NEXT   : CHECK AGAIN IN %s SECONDS", next_delay)
    logging.info("-----------------------------------------------")


# ============================================================
# MAIN COLLECTOR LOOP
# ============================================================

def main():
    """Menjalankan loop collector terus-menerus, membatasi buffer,
    lalu memilih interval normal atau interval retry."""
    names = [
        name.casefold()
        for name in REGISTERED_LOG_FILES
    ]

    if len(names) != len(set(names)):
        raise ValueError(
            "Duplicate registered filenames"
        )

    if any(
        os.path.basename(name) != name
        or not name.lower().endswith(".log")
        for name in REGISTERED_LOG_FILES
    ):
        raise ValueError(
            "Register filenames only, ending with .log"
        )

    db = sqlite3.connect(
        QUEUE_DB,
        timeout=30
    )

    init_queue(db)

    logging.info("=" * 55)
    logging.info("PYTHON LOG COLLECTOR V3 STARTED")
    logging.info("Source: %s", SOURCE_FOLDER)
    logging.info(
        "Registered files: %s",
        REGISTERED_LOG_FILES
    )
    logging.info("SQL: %s", SERVER)
    logging.info("Database: %s", DATABASE)
    logging.info("=" * 55)

    try:
        while True:
            try:
                queued = pending_count(db)
                collected = 0
                sources_ok = True

                # Jangan terus mengambil file jika SQLite antrean sudah penuh.
                # Read only while buffer has capacity
                if queued < MAX_PENDING:
                    remaining = min(
                        READ_LIMIT_PER_CYCLE,
                        MAX_PENDING - queued
                    )

                    for name in REGISTERED_LOG_FILES:
                        if remaining <= 0:
                            break

                        n, source_ok = collect_file(
                            db,
                            name,
                            remaining
                        )

                        collected += n
                        remaining -= n

                        sources_ok = (
                            sources_ok and source_ok
                        )

                # Jika antrean sebelum flush kosong, flush_queue tidak
                # membuka koneksi ke SQL Server. Jangan laporkan CONNECTED.
                pending_before_send = pending_count(db)
                sent, sql_ok = flush_queue(db)
                pending = pending_count(db)

                if pending_before_send == 0:
                    sql_status = "NOT CHECKED (QUEUE EMPTY)"
                elif sql_ok:
                    sql_status = "CONNECTED (LAST BATCH COMMITTED)"
                else:
                    sql_status = "ERROR / RETRY REQUIRED"

                if queued >= MAX_PENDING:
                    source_status = "NOT CHECKED (QUEUE FULL)"
                elif sources_ok:
                    source_status = "CONNECTED"
                else:
                    source_status = "UNAVAILABLE / RETRY REQUIRED"

                logging.info(
                    "Cycle: read=%s sent=%s pending=%s",
                    collected, sent, pending
                )

                # Pertahankan aturan jeda V3 yang sudah berjalan.
                if collected or (sent and pending):
                    interval = 0.1
                else:
                    interval = (
                        SYNC_INTERVAL
                        if sql_ok and sources_ok
                        else RETRY_INTERVAL
                    )

                display_status(
                    db, collected, sent, pending,
                    source_status, sql_status, interval
                )
                time.sleep(interval)

            except (
                OSError,
                sqlite3.Error
            ) as error:
                logging.exception(
                    "Collector loop failed: %s",
                    error
                )

                time.sleep(RETRY_INTERVAL)

    except KeyboardInterrupt:
        logging.info(
            "Collector stopped by user"
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
