"""Importa un respaldo SQLite a una base PostgreSQL vacía, sin cambiar claves.
Uso: DATABASE_URL=... python migrar_sqlite_postgres.py /ruta/respaldo.db
La URL se configura en el entorno; no se imprime ni se acepta por argumento.
"""
import sqlite3
import sys
from pathlib import Path
from database import DATABASE_URL, DATABASE_URL_UNPOOLED, PostgresConnection, init_postgres

TABLES = ('donantes', 'campanas', 'donaciones', 'turnos', 'avisos_campana')

def migrar(origen):
    if not DATABASE_URL: raise RuntimeError('Falta DATABASE_URL.')
    origen=Path(origen).resolve()
    if not origen.is_file(): raise RuntimeError('No existe el respaldo de origen.')
    fuente=sqlite3.connect(f'{origen.as_uri()}?mode=ro', uri=True)
    fuente.row_factory=sqlite3.Row
    try:
        fuente.execute('BEGIN')
        if fuente.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('El respaldo SQLite no supera integrity_check.')
        disponibles={r[0] for r in fuente.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'donantes','campanas','donaciones'} <= disponibles:
            raise RuntimeError('El respaldo no tiene las tablas necesarias.')
        init_postgres()
        destino=PostgresConnection(DATABASE_URL_UNPOOLED)
        try:
            destino.raw.execute('SELECT pg_advisory_xact_lock(742614271)')
            # Impide escrituras concurrentes durante la importación.
            destino.raw.execute('LOCK TABLE '+','.join(TABLES)+' IN ACCESS EXCLUSIVE MODE')
            for tabla in TABLES:
                if destino.raw.execute(f'SELECT COUNT(*) AS n FROM {tabla}').fetchone()['n']:
                    raise RuntimeError('La base destino contiene datos. No se mezcla ni se sobrescribe.')
            for tabla in TABLES:
                if tabla not in disponibles: continue
                columnas={r['column_name'] for r in destino.raw.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s",(tabla,)).fetchall()}
                filas=fuente.execute(f'SELECT * FROM {tabla}').fetchall()
                for fila in filas:
                    data={k:fila[k] for k in fila.keys() if k in columnas}
                    if tabla=='donantes':
                        data['estado_manual']={'Apto Manual':'Apto Clínico','No Apto Manual':'No Apto Clínico'}.get(data.get('estado_manual'),data.get('estado_manual','Automatico'))
                        fechas=[data.get(k) for k in ('fecha_ultimo_tatuaje','fecha_ultimo_piercing') if data.get(k)]
                        data['fecha_ultimo_tatuaje']=max(fechas) if fechas else None
                        data['fecha_ultimo_piercing']=None
                        for k in ('toma_medicacion','embarazo'):
                            if k in data:data[k]=int(bool(data[k]))
                    if tabla=='campanas' and 'publicada' in data:data['publicada']=int(bool(data['publicada']))
                    campos=','.join('"'+k+'"' for k in data)
                    valores=','.join('%s' for _ in data)
                    destino.raw.execute(f'INSERT INTO {tabla} ({campos}) VALUES ({valores})',tuple(data.values()))
                n=destino.raw.execute(f'SELECT COUNT(*) AS n FROM {tabla}').fetchone()['n']
                if n!=len(filas):raise RuntimeError('El conteo de importación no coincide.')
                if 'id' in columnas:
                    destino.raw.execute(f"SELECT setval(pg_get_serial_sequence('{tabla}','id'),COALESCE(MAX(id),1),MAX(id) IS NOT NULL) FROM {tabla}")
                print(tabla+': '+str(n)+' registros verificados')
            destino.commit()
        except Exception:
            destino.rollback();raise
        finally:destino.close()
    finally:fuente.close()
    print('Importación completada. Se conservaron los hashes de contraseña existentes.')

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Uso: python migrar_sqlite_postgres.py respaldo.db')
    migrar(sys.argv[1])
