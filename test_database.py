import os
os.environ.pop('DATABASE_URL',None)
import unittest
from database import sql_postgres, Record, Result

class DatabaseTest(unittest.TestCase):
    def test_parameters_and_date(self):
        self.assertEqual(sql_postgres("SELECT * FROM campanas WHERE localidad=? AND fecha>=date('now')"),"SELECT * FROM campanas WHERE localidad=%s AND fecha>=TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD')")
    def test_idempotent_queue(self):
        self.assertEqual(sql_postgres('INSERT OR IGNORE INTO avisos_campana(campana_id,donante_id) VALUES (?,?);'),'INSERT INTO avisos_campana(campana_id,donante_id) VALUES (%s,%s) ON CONFLICT DO NOTHING')
    def test_record_compatible_access(self):
        row=Record(id=3,nombre='Prueba')
        self.assertEqual(row[0],3)
        self.assertEqual(row['nombre'],'Prueba')
        self.assertEqual(dict(row),{'id':3,'nombre':'Prueba'})
    def test_result_empty(self):
        class Cursor:
            def fetchone(self):return None
            def fetchall(self):return []
        r=Result(Cursor())
        self.assertIsNone(r.fetchone());self.assertEqual(r.fetchall(),[])

if __name__=='__main__':unittest.main()
