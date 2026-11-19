"""Memory-efficient, dependency-free XLSX writer for the 5-column print log.

Creates a standards-based OOXML workbook using zipfile and incremental XML writes.
Source query is streamed using pyodbc.fetchmany() to keep memory bounded.
"""
import re
import zipfile
from datetime import date, datetime
from xml.sax.saxutils import escape

_INVALID_XML = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]')
_EXCEL_MAX_ROWS = 1_048_576


def _text(value):
    return escape(_INVALID_XML.sub('', str(value or ''))[:32767], {'"': '&quot;'})


def _date_serial(dt):
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, datetime.min.time())
    if not isinstance(dt, datetime):
        return None
    return (dt - datetime(1899, 12, 30)).total_seconds() / 86400.0


def _str_cell(ref, value, style=''):
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{_text(value)}</t></is></c>'


def _num_cell(ref, value, style=''):
    return f'<c r="{ref}"{style}><v>{value}</v></c>'


def export_xlsx(output_path, cursor, expected_count):
    """Write one worksheet. Caller must enforce a sensible export row cap."""
    if expected_count + 1 > _EXCEL_MAX_ROWS:
        raise ValueError('Excel sheet max row count exceeded')
    headers = ['No.', 'Date Printed', 'IP CODE', 'UNIQUE CODE', 'Raw Data']
    with zipfile.ZipFile(output_path, mode='w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>''')
        z.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        z.writestr('xl/workbook.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Print Logs" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        z.writestr('xl/_rels/workbook.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>''')
        z.writestr('xl/styles.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss.000"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1E2B4D"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>''')
        # Write compressed sheet XML incrementally without holding all rows in RAM.
        with z.open('xl/worksheets/sheet1.xml', mode='w', force_zip64=True) as out:
            def write(s):
                out.write(s.encode('utf-8'))
            write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
            write('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
            write('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
            write('<sheetFormatPr defaultRowHeight="16"/>')
            write('<cols><col min="1" max="1" width="9" customWidth="1"/><col min="2" max="2" width="26" customWidth="1"/><col min="3" max="4" width="20" customWidth="1"/><col min="5" max="5" width="70" customWidth="1"/></cols>')
            write('<sheetData><row r="1">')
            for col, value in zip('ABCDE', headers):
                write(_str_cell(f'{col}1', value, ' s="1"'))
            write('</row>')
            index = 0
            while True:
                batch = cursor.fetchmany(2000)
                if not batch:
                    break
                for r in batch:
                    index += 1
                    row = index + 1
                    write(f'<row r="{row}">')
                    write(_num_cell(f'A{row}', index))
                    dt_num = _date_serial(r[0])
                    if dt_num is not None:
                        write(_num_cell(f'B{row}', format(dt_num, '.12f'), ' s="2"'))
                    else:
                        write(_str_cell(f'B{row}', ''))
                    write(_str_cell(f'C{row}', r[1]))
                    write(_str_cell(f'D{row}', r[2]))
                    write(_str_cell(f'E{row}', r[3]))
                    write('</row>')
            write('</sheetData>')
            write(f'<autoFilter ref="A1:E{index + 1}"/>')
            write('</worksheet>')
    return index
