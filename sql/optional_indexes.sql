-- OPTIONAL: assess existing indexes and free space first.
-- SQL Server 2022 Express limit is 10 GB per database.
-- Run only during maintenance window; building indexes can be expensive.
USE LOG_COLLECTOR_DB;
GO
SELECT DB_NAME() AS DatabaseName;
EXEC sp_spaceused;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.LogEntries')
      AND name = N'IX_LogEntries_DatePrinted'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_LogEntries_DatePrinted
    ON dbo.LogEntries (LogDateTime DESC)
    INCLUDE (LogNumber, LogCode);
END;
GO
IF OBJECT_ID(N'dbo.LogEntriesV3', N'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'dbo.LogEntriesV3')
      AND name = N'IX_LogEntriesV3_DatePrinted'
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_LogEntriesV3_DatePrinted
    ON dbo.LogEntriesV3 (LogDateTime DESC)
    INCLUDE (LogNumber, LogCode);
END;
GO
