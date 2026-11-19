from app import app

with app.test_client() as client:
    resp = client.get('/')
    print('status:', resp.status_code)
