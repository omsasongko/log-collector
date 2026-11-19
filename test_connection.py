
import pyodbc

SERVER = r"WKS469-3ID\SQLEXPRESS"
DATABASE = "LOG_COLLECTOR_DB"

connection_string = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={SERVER};"
    f"DATABASE={DATABASE};"
    "Trusted_Connection=yes;"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
    "Connection Timeout=10;"
)

try:
    print("Connecting to SQL Server...")

    connection = pyodbc.connect(connection_string)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            @@SERVERNAME AS ServerName,
            DB_NAME() AS DatabaseName,
            COUNT_BIG(*) AS TotalRecords
        FROM dbo.LogEntries
    """)

    row = cursor.fetchone()

    print("Connection successful!")
    print(f"Server   : {row.ServerName}")
    print(f"Database : {row.DatabaseName}")
    print(f"Records  : {row.TotalRecords}")

    connection.close()

except pyodbc.Error as error:
    print("Connection failed!")
    print(error)
