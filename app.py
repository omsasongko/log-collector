"""PCS Print Log Dashboard — one-page, read-only Flask application.

Data: dbo.vAllLogEntries (history V1 + collector V3).
This service never updates, inserts, truncates, or deletes log data.
"""
from __future__ import annotations

import hmac
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, render_template, request, send_file, url_for
import pyodbc

from excel_writer import export_xlsx

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

SQL_SERVER = os.getenv('DB_SERVER', r'WKS469-3ID\SQLEXPRESS')
SQL_DATABASE = os.getenv('DB_NAME', 'LOG_COLLECTOR_DB')
SQL_DRIVER = os.getenv('ODBC_DRIVER', 'ODBC Driver 18 for SQL Server')
SQL_ENCRYPT = os.getenv('DB_ENCRYPT', 'yes')
SQL_TRUST_CERT = os.getenv('DB_TRUST_SERVER_CERT', 'yes')  # testing only: change for production
PER_PAGE = 20
PER_PAGE_OPTIONS = (20, 50, 100, 200, 500, 1000)
IP_PER_PAGE = 30
EXPORT_MAX_ROWS = max(1000, int(os.getenv('EXCEL_MAX_ROWS', '250000')))

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def optional_basic_auth(fn):
    """Optional HTTP Basic Auth; enable before binding Flask to a LAN interface."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected_user = os.getenv('DASHBOARD_USER', '').strip()
        expected_pass = os.getenv('DASHBOARD_PASSWORD', '')
        if expected_user and expected_pass:
            auth = request.authorization
            valid = bool(auth and
                         hmac.compare_digest(auth.username or '', expected_user) and
                         hmac.compare_digest(auth.password or '', expected_pass))
            if not valid:
                return Response('Authentication required', status=401,
                                headers={'WWW-Authenticate': 'Basic realm="PCS Log Dashboard"'})
        return fn(*args, **kwargs)
    return wrapper


def sql_connection():
    # Windows Integrated Authentication, matching SSMS account on WKS469-3ID.
    cs = (
        f'DRIVER={{{SQL_DRIVER}}};'
        f'SERVER={SQL_SERVER};'
        f'DATABASE={SQL_DATABASE};'
        'Trusted_Connection=yes;'
        f'Encrypt={SQL_ENCRYPT};'
        f'TrustServerCertificate={SQL_TRUST_CERT};'
        'Connection Timeout=10;'
    )
    return pyodbc.connect(cs, timeout=10, autocommit=True)


@contextmanager
def connected():
    """pyodbc's own with-block commits but does not reliably close the connection."""
    conn = sql_connection()
    try:
        yield conn
    finally:
        conn.close()


def query(cur, sql, params=(), timeout=60):
    # cur.timeout = timeout
    cur.execute(sql, *list(params))
    return cur


def latest_print_date(cur):
    # A per-table MAX can use date indexes, and avoids reading the large RawLog.
    query(cur, '''
        SELECT MAX(mx) FROM (
            SELECT MAX(LogDateTime) AS mx FROM dbo.LogEntries
            UNION ALL
            SELECT MAX(LogDateTime) AS mx FROM dbo.LogEntriesV3
        ) x
    ''')
    result = cur.fetchone()[0]
    return result.date() if result else None


def parse_date(value: str) -> date | None:
    if not value:
        return None
    value = value.strip()
    # Accept dd/mm/yyyy (UI default) as well as ISO yyyy-mm-dd.
    if len(value) == 10 and value[2] == '/' and value[5] == '/':
        try:
            day, month, year = int(value[0:2]), int(value[3:5]), int(value[6:10])
            return date(year, month, day)
        except ValueError as exc:
            raise ValueError('Format tanggal harus dd/mm/yyyy.') from exc
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Format tanggal harus dd/mm/yyyy.') from exc


def like_prefix(value: str) -> str:
    """Escape SQL Server LIKE wildcards; only prefix searching, not arbitrary wildcards."""
    escaped = value.replace('!', '!!').replace('%', '!%').replace('_', '!_').replace('[', '![')
    return escaped + '%'


SHIFT_RANGES = {
    '1': (0, 7),    # 00:00:00 – 07:59:59
    '2': (8, 15),   # 08:00:00 – 15:59:59
    '3': (16, 23),  # 16:00:00 – 23:59:59
}
SHIFT_LABELS = {'1': 'Shift 1 (00–08)', '2': 'Shift 2 (08–16)', '3': 'Shift 3 (16–24)'}


@dataclass(frozen=True)
class Filters:
    date_from: date | None
    date_to: date | None
    ip_code: str
    unique_code: str
    period: str
    range_mode: str
    shift: str  # 'all', '1', '2', '3'

    def params(self, per_page=PER_PAGE):
        return dict(
            date_from=self.date_from.isoformat() if self.date_from else '',
            date_to=self.date_to.isoformat() if self.date_to else '',
            ip_code=self.ip_code,
            unique_code=self.unique_code,
            range=self.range_mode,
            shift=self.shift,
            per_page=per_page,
        )

    def conditions(self):
        conditions = []
        params = []
        if self.date_from:
            conditions.append('LogDateTime >= ?')
            params.append(datetime.combine(self.date_from, datetime.min.time()))
        if self.date_to:
            conditions.append('LogDateTime < ?')
            params.append(datetime.combine(self.date_to + timedelta(days=1), datetime.min.time()))
        if self.ip_code:
            conditions.append("LogNumber LIKE ? ESCAPE '!'")
            params.append(like_prefix(self.ip_code))
        if self.unique_code:
            conditions.append("LogCode LIKE ? ESCAPE '!'")
            params.append(like_prefix(self.unique_code))
        return (' WHERE ' + ' AND '.join(conditions) if conditions else ''), params


def get_filters(args, cur):
    mode = args.get('range', '1')
    if mode not in {'1', '7', '30', '90', 'all', 'custom'}:
        mode = 'custom'
    ip_code = args.get('ip_code', '').strip()[:50]
    unique_code = args.get('unique_code', '').strip()[:100]
    shift = args.get('shift', 'all')
    if shift not in {'all', '1', '2', '3'}:
        shift = 'all'
    if mode in {'1', '7', '30', '90'}:
        yesterday = date.today() - timedelta(days=1)
        if mode == '1':
            # Kemarin (berdasarkan tanggal hari ini)
            start = end = yesterday
        else:
            # N hari terakhir, dihitung mundur dari kemarin
            start = yesterday - timedelta(days=int(mode) - 1)
            end = yesterday
    elif mode == 'all':
        start, end = None, None
    else:
        start = parse_date(args.get('date_from', '').strip())
        end = parse_date(args.get('date_to', '').strip())
    if start and end and start > end:
        raise ValueError('Tanggal mulai tidak boleh lebih besar dari tanggal akhir.')

    # Auto-determine chart grouping based on date range span
    if start and end:
        span = (end - start).days
        if span <= 31:
            period = 'day'
        elif span <= 90:
            period = 'week'
        else:
            period = 'month'
    elif mode == 'all':
        period = 'month'
    else:
        period = 'day'

    return Filters(start, end, ip_code, unique_code, period, mode, shift)


def fmt_date(value):
    return value.strftime('%d %b %Y, %H:%M:%S.%f')[:-3] if value else '—'


def trend_expression(period):
    # Hardcoded SQL expressions — user input is never added directly to SQL.
    if period == 'week':
        return "DATEADD(day, -(DATEDIFF(day, CONVERT(date,'19000101'), CONVERT(date,LogDateTime)) % 7), CONVERT(date,LogDateTime))"
    if period == 'month':
        return 'DATEFROMPARTS(YEAR(LogDateTime), MONTH(LogDateTime), 1)'
    return 'CONVERT(date, LogDateTime)'


def _shift_hour_cond(shift_key):
    """Return SQL condition for a specific shift (hardcoded, safe)."""
    lo, hi = SHIFT_RANGES[shift_key]
    return f'DATEPART(HOUR, LogDateTime) BETWEEN {lo} AND {hi}'


def _build_date_buckets(filters, by_date_map):
    """Generate ordered date bucket dicts from a {date: count} map, filling gaps with 0."""

    def align_start(d):
        if filters.period == 'week':
            return d - timedelta(days=d.weekday())
        if filters.period == 'month':
            return date(d.year, d.month, 1)
        return d

    def next_bucket(d):
        if filters.period == 'month':
            return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
        return d + timedelta(days=7 if filters.period == 'week' else 1)

    begin = align_start(filters.date_from or min(by_date_map))
    finish = align_start(filters.date_to or max(by_date_map))
    result = []
    d = begin
    while d <= finish:
        next_date = next_bucket(d)
        end = next_date - timedelta(days=1)
        if filters.period == 'week':
            label = f'{d:%d %b} – {end:%d %b}'
        elif filters.period == 'month':
            label = d.strftime('%b %Y')
        else:
            label = d.strftime('%d %b %Y')
        result.append({'label': label, 'start': d.isoformat(), 'end': end.isoformat(),
                       'count': by_date_map.get(d, 0)})
        if len(result) >= 2000:
            break
        d = next_date
    return result


def trend_series(cur, filters):
    """Return trend data. When shift='all', returns a dict with keys '1','2','3';
       otherwise returns a flat list (single series) for the selected shift."""
    where, params = filters.conditions()
    prefix = ' AND ' if where else ' WHERE '
    expr = trend_expression(filters.period)

    if filters.shift == 'all':
        # Query all three shifts in one pass using CASE grouping
        query(cur, f'''
            SELECT {expr} AS BucketDate,
                   CASE WHEN DATEPART(HOUR, LogDateTime) BETWEEN 0  AND 7  THEN 1
                        WHEN DATEPART(HOUR, LogDateTime) BETWEEN 8  AND 15 THEN 2
                        ELSE 3 END AS ShiftNum,
                   COUNT_BIG(*) AS PrintCount
            FROM dbo.vAllLogEntries
            {where}{prefix}LogDateTime IS NOT NULL
            GROUP BY {expr},
                     CASE WHEN DATEPART(HOUR, LogDateTime) BETWEEN 0  AND 7  THEN 1
                          WHEN DATEPART(HOUR, LogDateTime) BETWEEN 8  AND 15 THEN 2
                          ELSE 3 END
            ORDER BY BucketDate ASC, ShiftNum ASC
        ''', params, timeout=120)
        maps = {'1': {}, '2': {}, '3': {}}
        for row in cur.fetchall():
            d = row[0].date() if isinstance(row[0], datetime) else row[0]
            maps[str(int(row[1]))][d] = int(row[2])
        # Union all date keys so every shift has the same buckets
        all_dates = set()
        for m in maps.values():
            all_dates.update(m)
        if not all_dates:
            return {'1': [], '2': [], '3': []}
        for sk in maps:
            for d in all_dates:
                maps[sk].setdefault(d, 0)
        return {sk: _build_date_buckets(filters, maps[sk]) for sk in ('1', '2', '3')}
    else:
        # Single shift
        shift_cond = _shift_hour_cond(filters.shift)
        query(cur, f'''
            SELECT {expr} AS BucketDate, COUNT_BIG(*) AS PrintCount
            FROM dbo.vAllLogEntries
            {where}{prefix}LogDateTime IS NOT NULL AND {shift_cond}
            GROUP BY {expr}
            ORDER BY BucketDate ASC
        ''', params, timeout=120)
        by_date = {}
        for row in cur.fetchall():
            d = row[0].date() if isinstance(row[0], datetime) else row[0]
            by_date[d] = int(row[1])
        if not by_date:
            return []
        return _build_date_buckets(filters, by_date)


def hourly_dates(cur, filters):
    """Return distinct dates within filters for the hourly date-picker dropdown."""
    where, params = filters.conditions()
    prefix = ' AND ' if where else ' WHERE '
    query(cur, f'''
        SELECT DISTINCT CONVERT(date, LogDateTime) AS D
        FROM dbo.vAllLogEntries
        {where}{prefix}LogDateTime IS NOT NULL
        ORDER BY D DESC
    ''', params, timeout=120)
    return [r[0] if isinstance(r[0], date) else r[0].date() for r in cur.fetchall()]


def hourly_for_date(cur, target_date):
    """Return hourly counts (0–23) for a single date. No other filters applied."""
    start = datetime.combine(target_date, datetime.min.time())
    end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
    query(cur, '''
        SELECT DATEPART(HOUR, LogDateTime) AS H, COUNT_BIG(*) AS PrintCount
        FROM dbo.vAllLogEntries
        WHERE LogDateTime >= ? AND LogDateTime < ? AND LogDateTime IS NOT NULL
        GROUP BY DATEPART(HOUR, LogDateTime)
        ORDER BY H ASC
    ''', [start, end], timeout=120)
    by_hour = {int(r[0]): int(r[1]) for r in cur.fetchall()}
    series = [{'hour': h, 'label': f'{h:02d}:00', 'count': by_hour.get(h, 0)} for h in range(24)]
    peak = max(series, key=lambda p: p['count'])
    return {'series': series, 'peak_hour': peak['label'], 'peak_count': peak['count']}


def summary(cur, filters):
    where, params = filters.conditions()
    query(cur, f'''
        SELECT COUNT_BIG(*) AS TotalPrints,
               COUNT(DISTINCT LogNumber) AS UniqueIPCodes,
               MAX(LogDateTime) AS LastPrinted
        FROM dbo.vAllLogEntries {where}
    ''', params, timeout=120)
    row = cur.fetchone()
    return {'total': int(row[0] or 0), 'unique_ip': int(row[1] or 0), 'last_printed': fmt_date(row[2])}


def top_ip_codes(cur, filters):
    where, params = filters.conditions()
    extra = ' AND ' if where else ' WHERE '
    query(cur, f'''
        SELECT TOP (10) LogNumber AS IPCode, COUNT_BIG(*) AS PrintCount
        FROM dbo.vAllLogEntries
        {where}{extra}LogNumber IS NOT NULL AND LogNumber <> ''
        GROUP BY LogNumber
        ORDER BY PrintCount DESC, LogNumber ASC
    ''', params, timeout=120)
    return [{'code': str(r[0]), 'count': int(r[1])} for r in cur.fetchall()]


def log_page(cur, filters, page, per_page=PER_PAGE):
    where, params = filters.conditions()
    offset = (page - 1) * per_page
    query(cur, f'''
        SELECT ID, DataSource, LogDateTime, LogNumber, LogCode, RawLog
        FROM dbo.vAllLogEntries
        {where}
        ORDER BY LogDateTime DESC, DataSource DESC, ID DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    ''', [*params, offset, per_page], timeout=120)
    return [
        {
            'no': offset + idx + 1,
            'date_printed': fmt_date(r[2]),
            'ip_code': r[3] or '—',
            'unique_code': r[4] or '—',
            'raw': r[5] or '',
        }
        for idx, r in enumerate(cur.fetchall())
    ]


def respond_db_error(exc):
    app.logger.exception('SQL dashboard query failed')
    return render_template('error.html', message='Koneksi database bermasalah atau query terlalu lama.',
                           details=str(exc) if app.debug else ''), 503


@app.get('/')
@optional_basic_auth
def dashboard():
    try:
        with connected() as conn:
            cur = conn.cursor()
            filters = get_filters(request.args, cur)
            stats = summary(cur, filters)
            per_page = request.args.get('per_page', PER_PAGE, type=int) or PER_PAGE
            if per_page not in PER_PAGE_OPTIONS:
                per_page = PER_PAGE
            total_pages = max(1, math.ceil(stats['total'] / per_page))
            page = max(1, min(request.args.get('page', 1, type=int) or 1, total_pages))
            trend = trend_series(cur, filters)
            h_dates = hourly_dates(cur, filters)
            hourly_initial = hourly_for_date(cur, h_dates[0]) if h_dates else None
            ips = top_ip_codes(cur, filters)
            rows = log_page(cur, filters, page, per_page)
        base_args = filters.params(per_page=per_page)
        page_links = [
            {'number': n, 'url': url_for('dashboard', **base_args, page=n), 'active': n == page}
            for n in range(max(1, page - 2), min(total_pages, page + 2) + 1)
        ]
        # trend_length: number of date buckets (same across all shifts)
        if isinstance(trend, dict):
            trend_length = max((len(v) for v in trend.values()), default=0)
        else:
            trend_length = len(trend)
        return render_template(
            'dashboard.html', filters=filters, stats=stats, trend=trend,
            trend_length=trend_length,
            hourly_dates=[d.isoformat() for d in h_dates],
            hourly_initial=hourly_initial,
            ips=ips, rows=rows, page=page, page_links=page_links, total_pages=total_pages,
            per_page=per_page, per_page_options=PER_PAGE_OPTIONS,
            page_start=(page - 1) * per_page + 1 if stats['total'] else 0,
            page_end=min(page * per_page, stats['total']),
            previous=url_for('dashboard', **base_args, page=page-1) if page > 1 else None,
            next_page=url_for('dashboard', **base_args, page=page+1) if page < total_pages else None,
            export_link=url_for('export_excel', **base_args),
            query_params=base_args, excel_max=EXPORT_MAX_ROWS,
            errors=[],
        )
    except ValueError as exc:
        return render_template('error.html', message=str(exc), details=''), 400
    except (pyodbc.Error, TimeoutError) as exc:
        return respond_db_error(exc)


@app.get('/api/hourly')
@optional_basic_auth
def api_hourly():
    """Return hourly print counts for a single date (AJAX endpoint)."""
    try:
        d = parse_date(request.args.get('date', '').strip())
        if not d:
            return jsonify({'error': 'Parameter date wajib diisi.'}), 400
        with connected() as conn:
            cur = conn.cursor()
            data = hourly_for_date(cur, d)
        return jsonify(data)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except (pyodbc.Error, TimeoutError):
        app.logger.exception('Hourly query failed')
        return jsonify({'error': 'SQL Server tidak dapat memproses permintaan.'}), 503


@app.get('/api/ip-codes')
@optional_basic_auth
def ip_code_breakdown():
    try:
        with connected() as conn:
            cur = conn.cursor()
            filters = get_filters(request.args, cur)
            where, params = filters.conditions()
            extra = ' AND ' if where else ' WHERE '
            base = f'{where}{extra}LogNumber IS NOT NULL AND LogNumber <> \'\''
            query(cur, f'''SELECT COUNT(DISTINCT LogNumber)
                           FROM dbo.vAllLogEntries {base}''', params, timeout=120)
            total_codes = int(cur.fetchone()[0] or 0)
            pages = max(1, math.ceil(total_codes / IP_PER_PAGE))
            page = max(1, min(request.args.get('ip_page', 1, type=int) or 1, pages))
            offset = (page - 1) * IP_PER_PAGE
            query(cur, f'''
                SELECT LogNumber, COUNT_BIG(*) AS PrintCount
                FROM dbo.vAllLogEntries {base}
                GROUP BY LogNumber
                ORDER BY PrintCount DESC, LogNumber ASC
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            ''', [*params, offset, IP_PER_PAGE], timeout=120)
            items = [{'ip_code': str(r[0]), 'prints': int(r[1])} for r in cur.fetchall()]
        return jsonify({'items': items, 'page': page, 'pages': pages, 'total_codes': total_codes})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except (pyodbc.Error, TimeoutError):
        app.logger.exception('IP breakdown failed')
        return jsonify({'error': 'SQL Server tidak dapat memproses permintaan.'}), 503


@app.get('/export.xlsx')
@optional_basic_auth
def export_excel():
    """Download only filtered rows. A size cap prevents accidental enormous exports."""
    path = None
    try:
        with connected() as conn:
            cur = conn.cursor()
            filters = get_filters(request.args, cur)
            where, params = filters.conditions()
            query(cur, f'SELECT COUNT_BIG(*) FROM dbo.vAllLogEntries {where}', params, timeout=120)
            count = int(cur.fetchone()[0] or 0)
            if count > EXPORT_MAX_ROWS:
                return render_template(
                    'error.html',
                    message=(f'Hasil filter berisi {count:,} record. Maksimum ekspor Excel '
                             f'{EXPORT_MAX_ROWS:,} record per file. Persempit date range/IP CODE/UNIQUE CODE terlebih dahulu.'),
                    details='',
                ), 422
            if count == 0:
                return render_template('error.html', message='Tidak ada record yang dapat diekspor.', details=''), 404

            query(cur, f'''
                SELECT LogDateTime, LogNumber, LogCode, RawLog
                FROM dbo.vAllLogEntries {where}
                ORDER BY LogDateTime DESC, DataSource DESC, ID DESC
            ''', params, timeout=180)
            fd, path = tempfile.mkstemp(suffix='.xlsx', prefix='pcs_print_')
            os.close(fd)
            export_xlsx(path, cur, count)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        response = send_file(path, as_attachment=True,
                             download_name=f'PCS_Print_Log_{stamp}.xlsx',
                             mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response.call_on_close(lambda: safe_unlink(path))
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ValueError as exc:
        if path:
            safe_unlink(path)
        return render_template('error.html', message=str(exc), details=''), 400
    except (pyodbc.Error, OSError, TimeoutError) as exc:
        if path:
            safe_unlink(path)
        return respond_db_error(exc)


def safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


@app.after_request
def add_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; object-src 'none'; form-action 'self'"
    )
    return response


if __name__ == '__main__':
    # Local Windows testing only. Use run.py (Waitress) for steady operation.
    app.run(host='127.0.0.1', port=5000, debug=False)
