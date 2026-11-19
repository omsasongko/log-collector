# PCS Print Insights — Flask Single Page Dashboard

Dibuat berdasarkan kebutuhan `CMSMarketinglabel.log` pada WKS469-15ID dengan database `LOG_COLLECTOR_DB` di SQL Server Express `WKS469-3ID\SQLEXPRESS`. Tampilan terinspirasi dari layout **Humdash v1.0.0** yang dilampirkan (kartu KPI, sidebar, grafik, tabel), dengan CSS/JS original dan **tanpa dependensi CDN**. Script ini tidak menyertakan kode sumber Humdash dan tidak menjalankan proses npm/pnpm.

## 1. Apa yang sudah ada

- **Satu halaman** dashboard `/` dengan sidebar navigasi anchor ke section.
- **Kolom tabel** No., Date Printed (`LogDateTime`), IP CODE (`LogNumber`), UNIQUE CODE (`LogCode`), Raw Data (`RawLog`).
- **Filter:** tanggal mulai/sampai, quick range Kemarin/7 hari terakhir, semua histori, IP CODE dan UNIQUE CODE (pencarian awalan).
- **Grafik jumlah print:** per hari, per minggu (Senin–Minggu), atau per bulan, berdasarkan `LogDateTime`. Klik titik grafik untuk fokus pada tanggal/periode tersebut.
- **Grafik/bar IP CODE terbanyak** dan tombol **Lihat semua IP CODE** dengan pagination; klik kode untuk memfilter tabel.
- **Ekspor Excel `.xlsx`** sesuai filter (default maksimal 250.000 record dalam satu file). Excel memiliki kolom yang sama dan filter header; `Date Printed` berupa datetime Excel, bukan teks.
- **20 baris per halaman secara default** (dapat dipilih 20/50/100/200/500/1000), query SQL di sisi server. Penomoran `No.` melanjutkan nomor sesuai halaman.
- **Read-only**: semua perintah DB `SELECT` saja; tidak mengubah collector, checkpoint, atau tabel log.

> **Definisi:** Dashboard menghitung **1 record log = 1 print**. Ini asumsi analisis. Jika PCS mencatat event non-print di file yang sama, perlu filter event khusus berdasarkan format asli agar angka mencerminkan jumlah label benar-benar dicetak.

## 2. Instalasi pada WKS469-3ID

**Jangan hentikan `collector.py` yang sudah berjalan.** Dashboard adalah proses terpisah. Jika sudah ada `app.py`, backup dahulu.

1. Extract paket ZIP ke folder sementara (contoh `C:\Temp\pcs-log-dashboard`).
2. Copy berikut ke `C:\laragon\www\log-collector\`:

   - `app.py`
   - `requirements.txt`
   - `run.py`
   - `excel_writer.py`
   - folder `templates\` dan `static\`
   - `.env.example` menjadi `.env`
   - `.htaccess` di root proyek agar Apache Laragon tidak menyajikan source Python, .env, atau buffer SQLite.

   **Jangan menimpa `collector.py` maupun folder `storage\`.**

3. Dari PowerShell baru:

   ```powershell
   cd C:\laragon\www\log-collector
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   Copy-Item .env.example .env
   notepad .env
   ```

   Jika `requirements.txt` lama dipakai untuk collector, tidak masalah menggantinya dengan versi yang menambahkan `waitress`. Library Python tetap berada di `.venv` lokal.

4. Tes query via SSMS menggunakan `sql\check_read_only.sql`, pastikan `dbo.vAllLogEntries` ada dan menampilkan data.

5. Jalankan dashboard (terminal kedua):

   ```powershell
   .\.venv\Scripts\python.exe run.py
   ```

6. Buka **http://127.0.0.1:5000** di browser pada WKS469-3ID.

Jika port 5000 dipakai aplikasi lain, ubah `DASHBOARD_PORT=5050` di `.env` dan akses `http://127.0.0.1:5050`.

## 3. Yang tidak perlu diubah

- Tidak perlu membuat tabel baru atau menghapus data lama.
- Tidak perlu mengubah `collector.py`, `collector_queue.sqlite3`, atau `dbo.CollectorCheckpoint`.
- Dashboard hanya membaca **view** `dbo.vAllLogEntries` (histori `dbo.LogEntries` + data baru `dbo.LogEntriesV3`).

## 4. Operasi UI

- Filter default = **Kemarin** (berdasarkan tanggal hari ini). Quick range 7 hari dihitung mundur dari kemarin, bukan dari tanggal print terbaru di SQL.
- Dropdown **Quick Range** menentukan rentang otomatis. Mengubah From/To Date otomatis memilih **Custom date range**.
- Klik titik pada grafik line untuk melihat print + IP CODE pada periode tersebut.
- Klik batang IP CODE atau tombol `Lihat logs` untuk mengisi filter kode.
- **Export Excel** mengikuti filter saat itu. Jika lebih dari 250.000 record, tampil pesan untuk mempersempit range. Batas dapat diubah lewat `EXCEL_MAX_ROWS`, namun sebaiknya hanya jika storage dan waktu pemrosesan memadai. Excel maksimum 1.048.576 baris per sheet (termasuk header), versi ini membuat satu sheet dan menolak lebih dari maksimum tersebut.
- `No.` adalah nomor urut hasil filter, bukan `LogEntries.ID`.

## 5. Kinerja SQL Server Express

- Database lama berisi ±3,7 juta record. Filter tanggal disusun agar `LogDateTime` dapat memakai index.
- Untuk kinerja lebih baik, tersedia `sql\optional_indexes.sql`. **Jangan jalankan sembarangan**: cek ukuran DB, backup, kapasitas kosong, lalu jalankan pada maintenance window karena SQL Express 2022 memiliki limit 10 GB per database.
- Query chart/all data, `COUNT(DISTINCT ...)`, dan halaman sangat dalam mungkin masih lambat pada perangkat dengan RAM/disk terbatas. Pilih rentang tanggal yang lebih sempit; tahap produksi bisa menambah summary table/aggregation harian terjadwal jika diperlukan.
- Endpoint ekspor Excel menulis file sementara di folder temp Windows dan melakukan fetch SQL bertahap; tidak me-load 250.000 record ke memori sekaligus. File sementara normalnya dihapus ketika respons selesai.

## 6. Keamanan sebelum dipakai melalui LAN

- Default host `127.0.0.1`, jadi **hanya** bisa dibuka pada WKS469-3ID itu sendiri.
- Jangan langsung mengganti host menjadi `0.0.0.0` dan mempublikasikan port Flask tanpa kontrol akses. Kalau ingin diakses komputer lain, aktifkan user/password untuk dashboard, gunakan reverse proxy HTTPS (mis. IIS/Nginx) yang mengatur TLS, dan batasi IP firewall. HTTP Basic Auth sendiri mengirim kredensial dalam bentuk yang mudah didekode jika tidak memakai HTTPS.
- Gunakan Windows account **read-only** pada `dbo.vAllLogEntries` untuk proses dashboard, berbeda dengan akun yang collector gunakan untuk INSERT.
- `.htaccess` melindungi root folder project dari Apache 2.4 ketika Laragon berjalan, tapi **tidak menggantikan proteksi Windows ACL/NTFS**.
- `DB_TRUST_SERVER_CERT=yes` adalah pengaturan tahap uji. Gunakan sertifikat SQL Server tepercaya dan `DB_TRUST_SERVER_CERT=no` untuk produksi.

## 7. Struktur paket

```text
pcs-log-dashboard/
├── app.py                # Flask routes, SQL queries, filtering, export
├── excel_writer.py       # Stream XLSX langsung dengan ZIP/XML
├── run.py                # Waitress runner, no Flask debug
├── templates/
│   ├── dashboard.html    # 1-page UI
│   └── error.html
├── static/
│   ├── style.css
│   ├── app.js            # SVG chart + IP CODE breakdown dialog
│   └── favicon.svg
├── sql/
│   ├── check_read_only.sql
│   └── optional_indexes.sql
├── requirements.txt
├── .env.example
├── .htaccess
└── .gitignore
```

## 8. Pengujian sebelum produksi

1. Collector import lama tetap berjalan; dashboard membaca record tanpa mengunci file log sumber.
2. Filter tanggal kosong, 1 hari, 1 minggu, beberapa bulan, dan semua histori.
3. Filter IP CODE dan UNIQUE CODE; cocokkan jumlah dengan query SSMS.
4. Klik chart harian, cek kode per tanggal; cek ringkasan modal dan nomor halaman.
5. Export Excel untuk rentang kecil, buka di Excel dan cocokkan 5 kolom.
6. Tes SQL Server disconnect: dashboard menunjukkan pesan error dan pulih setelah koneksi kembali; collector berjalan terpisah.

**Catatan teknis:** Paket telah dicek sintaks dan ekspor XLSX secara offline, tetapi perlu uji langsung terhadap SQL Server pada WKS469-3ID sebelum penggunaan produksi.
