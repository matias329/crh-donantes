"""Copia consistente de SQLite. Uso: python backup_db.py /ruta/respaldo.db"""
import os
import sqlite3
import sys
from pathlib import Path

if __name__ == '__main__':
    origen = Path(os.environ.get('DB_PATH', Path(__file__).with_name('patients.db'))).resolve()
    destino = Path(sys.argv[1]).resolve()
    if not origen.is_file() or destino.exists():
        raise SystemExit('Origen inexistente o destino ya ocupado. No se sobrescribe.')
    destino.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f'{origen.as_uri()}?mode=ro', uri=True) as fuente:
        with sqlite3.connect(destino) as salida:
            fuente.backup(salida)
            if salida.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise SystemExit('Verificación del respaldo fallida.')
    os.chmod(destino, 0o600)
    print('Respaldo verificado:', destino)
