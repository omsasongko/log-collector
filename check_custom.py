from app import app

with app.test_client() as client:
    resp = client.get('/?range=custom&date_from=09/11/2026&date_to=13/11/2026')
    print('status:', resp.status_code)
    html = resp.get_data(as_text=True)
    print('error page:', 'error.html' in html or 'Koneksi database' in html or 'Format tanggal' in html)
    if 'Format tanggal' in html:
        print('DATE FORMAT ERROR')
