"""Backend PostgreSQL explícito. SQLite queda disponible solo para desarrollo."""
import os
import re
import sqlite3

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
DATABASE_URL_UNPOOLED = os.environ.get('DATABASE_URL_UNPOOLED', DATABASE_URL).strip()
IntegrityError = (sqlite3.IntegrityError,)
OperationalError = (sqlite3.OperationalError,)
if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row
    IntegrityError += (psycopg.IntegrityError,)
    OperationalError += (psycopg.OperationalError,)


class Record(dict):
    """Columnas por nombre y posición, compatible con las consultas existentes."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def sql_postgres(sql):
    sql = sql.replace("date('now')", "TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD')")
    ignore = bool(re.match(r'\s*INSERT OR IGNORE\b', sql, re.I))
    if ignore:
        sql = re.sub(r'INSERT OR IGNORE', 'INSERT', sql, count=1, flags=re.I)
        sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
    # Todas las consultas del proyecto usan ? exclusivamente como marcador.
    return sql.replace('?', '%s')


class Result:
    def __init__(self, cursor, lastrowid=None):
        self.cursor = cursor
        self.lastrowid = lastrowid
    def fetchone(self):
        row = self.cursor.fetchone()
        return Record(row) if row is not None else None
    def fetchall(self):
        return [Record(row) for row in self.cursor.fetchall()]
    def __iter__(self):
        return iter(self.fetchall())


class PostgresConnection:
    def __init__(self, url=None):
        # No se registra la URL ni se usan credenciales dentro del código.
        self.raw = psycopg.connect(url or DATABASE_URL, connect_timeout=10, row_factory=dict_row)
    def execute(self, sql, params=()):
        sql = sql_postgres(sql)
        returning = bool(re.match(r'\s*INSERT INTO campanas\b', sql, re.I))
        if returning:
            sql = sql.rstrip().rstrip(';') + ' RETURNING id'
        cursor = self.raw.execute(sql, params)
        rowid = cursor.fetchone()['id'] if returning else None
        return Result(cursor, rowid)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()


SCHEMA = [
'''CREATE TABLE IF NOT EXISTS donantes (
 id BIGSERIAL PRIMARY KEY, dni TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL,
 apellido TEXT NOT NULL DEFAULT '', nombres TEXT NOT NULL DEFAULT '',
 fecha_nacimiento TEXT NOT NULL, genero TEXT NOT NULL,
 grupo_sanguineo TEXT NOT NULL, factor_rh TEXT NOT NULL, localidad TEXT NOT NULL,
 email TEXT UNIQUE NOT NULL, telefono TEXT, password_hash TEXT NOT NULL,
 estado_manual TEXT DEFAULT 'Automatico', observaciones_medicas TEXT DEFAULT '',
 fecha_ultimo_tatuaje TEXT, fecha_ultimo_piercing TEXT, toma_medicacion INTEGER DEFAULT 0,
 peso_kg REAL, embarazo INTEGER DEFAULT 0)''',
'''CREATE TABLE IF NOT EXISTS campanas (
 id BIGSERIAL PRIMARY KEY, titulo TEXT NOT NULL, tipo TEXT NOT NULL, fecha TEXT NOT NULL,
 ubicacion TEXT NOT NULL, localidad TEXT NOT NULL,
 horario TEXT NOT NULL DEFAULT '08:00 a 12:00', publicada INTEGER NOT NULL DEFAULT 1)''',
'''CREATE TABLE IF NOT EXISTS donaciones (
 id BIGSERIAL PRIMARY KEY, donante_id BIGINT NOT NULL REFERENCES donantes(id),
 fecha_donacion TEXT NOT NULL, volumen_ml INTEGER, observaciones TEXT,
 lugar TEXT NOT NULL DEFAULT 'Centro Regional de Hemoterapia de Jujuy',
 campana_id BIGINT, grupo_extraccion TEXT, rh_extraccion TEXT, localidad_extraccion TEXT)''',
'''CREATE TABLE IF NOT EXISTS turnos (
 id BIGSERIAL PRIMARY KEY, donante_id BIGINT NOT NULL REFERENCES donantes(id),
 campana_id BIGINT NOT NULL REFERENCES campanas(id), hora TEXT NOT NULL,
 UNIQUE(donante_id,campana_id))''',
'''CREATE TABLE IF NOT EXISTS avisos_campana (
 campana_id BIGINT NOT NULL, donante_id BIGINT NOT NULL,
 enviado INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(campana_id,donante_id))''',
'CREATE INDEX IF NOT EXISTS idx_donaciones_donante_fecha ON donaciones(donante_id,fecha_donacion)',
'CREATE INDEX IF NOT EXISTS idx_turnos_campana_hora ON turnos(campana_id,hora)',
]


def init_postgres():
    if not DATABASE_URL:
        raise RuntimeError('Falta DATABASE_URL. No se inicializa PostgreSQL.')
    conn = PostgresConnection(DATABASE_URL_UNPOOLED)
    try:
        # Serializa migraciones de workers que arrancan juntos.
        conn.raw.execute('SELECT pg_advisory_xact_lock(742614271)')
        # No adaptar a ciegas una base de otro producto o de un esquema anterior.
        existentes = conn.raw.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name IN ('donantes','campanas','donaciones','turnos','avisos_campana','crh_schema_version')").fetchall()
        tablas = {r['table_name'] for r in existentes}
        if tablas:
            if 'crh_schema_version' not in tablas:
                raise RuntimeError('La base tiene un esquema sin identificar. Requiere respaldo y revisión; no se modifica.')
            version = conn.raw.execute('SELECT version FROM crh_schema_version').fetchone()
            if not version or version['version'] != 27:
                raise RuntimeError('Versión de base incompatible. No se modifica.')
        for statement in SCHEMA:
            conn.raw.execute(statement)
        conn.raw.execute('CREATE TABLE IF NOT EXISTS crh_schema_version(version INTEGER PRIMARY KEY)')
        conn.raw.execute('INSERT INTO crh_schema_version(version) VALUES (27) ON CONFLICT DO NOTHING')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
