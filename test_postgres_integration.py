"""Ejecutar solo contra una base PostgreSQL de pruebas vacía."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

@unittest.skipUnless(os.environ.get('DATABASE_URL'), 'Requiere PostgreSQL de pruebas')
class PostgresIntegration(unittest.TestCase):
    def test_import_http_write_and_reconnect(self):
        import database
        import migrar_sqlite_postgres
        # Fixture mínima SQLite con hashes reales; jamás usa datos del repositorio.
        from werkzeug.security import generate_password_hash, check_password_hash
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.db'
            with sqlite3.connect(path) as source:
                source.execute('''CREATE TABLE donantes(id INTEGER PRIMARY KEY,dni TEXT,nombre TEXT,
                    fecha_nacimiento TEXT,genero TEXT,grupo_sanguineo TEXT,factor_rh TEXT,localidad TEXT,
                    email TEXT,telefono TEXT,password_hash TEXT,estado_manual TEXT,peso_kg REAL)''')
                source.execute("INSERT INTO donantes VALUES (1,'12345678','Prueba Persona','1990-01-01','M','O','+','Perico','prueba@example.com','3881234567',?,'Apto Manual',70)",(generate_password_hash('fixture-password'),))
                source.execute('CREATE TABLE campanas(id INTEGER PRIMARY KEY,titulo TEXT,tipo TEXT,fecha TEXT,ubicacion TEXT,localidad TEXT)')
                source.execute('CREATE TABLE donaciones(id INTEGER PRIMARY KEY,donante_id INTEGER,fecha_donacion TEXT)')
            migrar_sqlite_postgres.migrar(path)
            # Una segunda importación debe detenerse sin perder registros.
            with self.assertRaisesRegex(RuntimeError,'contiene datos'):
                migrar_sqlite_postgres.migrar(path)
        database.init_postgres()
        conn=database.PostgresConnection()
        donor=conn.execute('SELECT * FROM donantes WHERE id=?',(1,)).fetchone()
        self.assertEqual(donor['estado_manual'],'Apto Clínico')
        self.assertTrue(check_password_hash(donor['password_hash'],'fixture-password'))
        conn.close()
        import app
        client=app.app.test_client()
        for page in ['/', '/registro','/login']:
            self.assertEqual(client.get(page).status_code,200)
        client.get('/registro')
        with client.session_transaction() as session:token=session['csrf_token']
        response=client.post('/registro',data=dict(csrf_token=token,dni='23456789',apellido='Prueba',nombres='Nueva',fecha_nacimiento='1991-01-01',genero='F',grupo_sanguineo='A',factor_rh='+',localidad='Perico',email='nueva@example.com',telefono='3887654321',peso_kg='70',password='nueva-password'))
        self.assertEqual(response.status_code,302)
        with client.session_transaction() as session:
            session['admin_logged']=True
        for page in ['/admin','/admin/estadisticas','/admin/notificaciones','/admin/paciente/1']:
            self.assertEqual(client.get(page).status_code,200,page)
        client.get('/admin/campanas/nueva')
        with client.session_transaction() as session:token=session['csrf_token']
        response=client.post('/admin/campanas/nueva',data=dict(csrf_token=token,titulo='Colecta de prueba',fecha='2099-12-10',horario='08:00 a 12:00',ubicacion='Plaza',localidad='Perico',publicada='1'))
        self.assertEqual(response.status_code,302)
        conn=database.PostgresConnection()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM donantes').fetchone()[0],2)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM avisos_campana').fetchone()[0],2)
        conn.close()

if __name__=='__main__':unittest.main()
