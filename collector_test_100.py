
import re
import pyodbc

SOURCE = (
    r"\\WKS469-15ID\Users\sa.market04id"
    r"\AppData\Roaming\PCS\CMSMarketinglabel.log"
)

SERVER = r"WKS469-3ID\SQLEXPRESS"
DATABASE = "LOG_COLLECTOR_DB"

# Pengujian awal: maksimum 100 baris
LIMIT = 100

PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) "
    r"(\d{2})\.(\d{2})\.(\d{2})\.(\d{3})"
    r"\s+-\s+(\d+)\s+-\s+(\S+)$"
)

def parse_log(raw):
    match = PATTERN.match(raw.strip())

    if not match:
        return None, None, None

    date, hh, mm, ss, ms, number, code = match.groups()

    timestamp = f"{date} {hh}:{mm}:{ss}.{ms}"

    return timestamp, number, code


def main():
    connection_string = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )

    connection = pyodbc.connect(connection_string)
    cursor = connection.cursor()

    inserted = 0
    processed = 0

    try:
        with open(SOURCE, "rb") as file:
            while processed < LIMIT:
                offset = file.tell()
                raw_bytes = file.readline()

                if not raw_bytes:
                    break

                # Jangan proses baris yang belum lengkap
                if not raw_bytes.endswith(b"\n"):
                    break

                raw = raw_bytes.decode(
                    "utf-8", errors="replace"
                ).rstrip("\r\n")

                timestamp, number, code = parse_log(raw)

                cursor.execute("""
                    IF NOT EXISTS (
                        SELECT 1
                        FROM dbo.LogEntries WITH (UPDLOCK, HOLDLOCK)
                        WHERE SourceFile = ? AND ByteOffset = ?
                    )
                    INSERT INTO dbo.LogEntries (
                        SourceFile,
                        ByteOffset,
                        LogDateTime,
                        LogNumber,
                        LogCode,
                        RawLog
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    SOURCE, offset,
                    SOURCE, offset,
                    timestamp, number, code, raw
                )

                processed += 1

        connection.commit()

        cursor.execute("""
            SELECT COUNT_BIG(*)
            FROM dbo.LogEntries
        """)

        inserted = cursor.fetchone()[0]

        print("Import completed!")
        print(f"Lines processed : {processed}")
        print(f"Total SQL rows  : {inserted}")

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


if __name__ == "__main__":
    main()
