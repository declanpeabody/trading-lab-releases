"""Local-only service. Credentials do not appear in the login-service plist."""
import json
import os
from pathlib import Path

def main():
    base=Path(__file__).resolve().parent
    os.umask(0o077)
    config=json.loads((base/'config.json').read_text())
    for key in ('DASHBOARD_USER','DASHBOARD_PASSWORD','TWELVE_DATA_API_KEY'):
        os.environ[key]=config.get(key,'')
    os.environ.update(DATA_DIR=str(base/'data'),BIND_HOST='127.0.0.1',PORT='8765')
    from app import main as serve
    serve()

if __name__=='__main__': main()
