"""Run the Flask dashboard using Waitress (separate from the collector)."""
import os
from dotenv import load_dotenv
from waitress import serve
from app import app

load_dotenv()
host = os.getenv('DASHBOARD_HOST', '127.0.0.1')
port = int(os.getenv('DASHBOARD_PORT', '5000'))
print(f'PCS Print Log Dashboard listening at http://{host}:{port}')
if host not in {'127.0.0.1', 'localhost', '::1'} and not (
    os.getenv('DASHBOARD_USER') and os.getenv('DASHBOARD_PASSWORD')
):
    raise SystemExit('Untuk akses LAN, isi DASHBOARD_USER dan DASHBOARD_PASSWORD di .env terlebih dahulu.')
serve(app, host=host, port=port, threads=4, channel_timeout=300)
