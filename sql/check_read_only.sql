USE LOG_COLLECTOR_DB;
GO
SELECT OBJECT_ID('dbo.vAllLogEntries', 'V') AS ViewId,
       OBJECT_ID('dbo.LogEntries', 'U') AS OldTableId,
       OBJECT_ID('dbo.LogEntriesV3', 'U') AS NewTableId;
SELECT TOP (5) LogDateTime, LogNumber, LogCode, RawLog
FROM dbo.vAllLogEntries ORDER BY LogDateTime DESC;
GO
