"""Pruebas de lógica y SQL sin dependencias web; se extraen funciones reales por AST.
No reemplazan las pruebas HTTP de test_app.py.
"""
import ast
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta
from typing import Optional
import unittest
from types import SimpleNamespace

SOURCE = Path(__file__).with_name('app.py').read_text()
TREE = ast.parse(SOURCE)
NAMES = {'_configurar_conexion', 'init_db_if_missing', 'calcular_edad',
         'calcular_elegibilidad', 'validar_nueva_donacion', 'validar_antecedentes',
         'validar_nombre', 'validar_telefono', 'validar_campana', 'obtener_historial',
         'preparar_avisos_campana', 'admin_estadisticas', 'admin_notificaciones', 'campanas_activas'}
ENV = dict(sqlite3=sqlite3, datetime=datetime, timedelta=timedelta, Optional=Optional,
           os=os, re=re, BLOOD_GROUPS=['A','B','AB','O','No sé'], RH_OPTIONS=['+','-','No sé'],
           LOCALIDADES=['Perico','Tilcara'], ZONAS={'Valles':['Perico']},
           EDAD_MINIMA=18, EDAD_MAXIMA=65, VENTANA_DIAS={'M':60,'F':60},
           MAX_DONACIONES_ANUALES={'M':4,'F':3})
from urllib.parse import quote
ENV['quote'] = quote
nodes = []
for n in TREE.body:
    if isinstance(n, ast.FunctionDef) and n.name in NAMES:
        n.decorator_list = []
        nodes.append(n)
exec(compile(ast.Module(nodes, type_ignores=[]), 'app.py', 'exec'), ENV)

class Params(dict):
    def get(self,k,default=None,type=None):
        v=super().get(k,default)
        if type:
            try:return type(v)
            except (TypeError,ValueError):return default
        return v

class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'data.db'
        ENV['DB_PATH']=str(self.path)
        ENV['init_db_if_missing']()
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        ENV['get_db']=lambda:self.db
        ENV['admin_requerido']=lambda:True
        ENV['render_template']=lambda template,**kw:kw
        ENV['fecha_legible']=lambda x:x
        def abort(code,*args):raise ValueError(code)
        ENV['abort']=abort
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def donor(self,dni='12345678',**extra):
        data=dict(dni=dni,nombre='Prueba Persona',fecha_nacimiento='1990-01-01',genero='M',grupo_sanguineo='O',factor_rh='+',localidad='Perico',email=dni+'@example.com',telefono='3881234567',password_hash='hash',estado_manual='Automatico',peso_kg=70,embarazo=0,toma_medicacion=0)
        data.update(extra)
        self.db.execute(f"INSERT INTO donantes ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",tuple(data.values()));self.db.commit()
        return self.db.execute('SELECT * FROM donantes WHERE dni=?',(dni,)).fetchone()
    def campaign(self):
        cur=self.db.execute("INSERT INTO campanas(titulo,tipo,fecha,ubicacion,localidad,publicada) VALUES ('Colecta','Sangre','2099-12-10','Plaza','Perico',1)")
        self.db.commit();return cur.lastrowid
    def test_migration_keeps_rows_passwords_and_latest_antecedent(self):
        d=self.donor(estado_manual='Apto Manual',fecha_ultimo_tatuaje='2020-01-01',fecha_ultimo_piercing='2025-01-01')
        ENV['init_db_if_missing']();ENV['init_db_if_missing']()
        row=self.db.execute('SELECT * FROM donantes WHERE id=?',(d['id'],)).fetchone()
        self.assertEqual(row['password_hash'],'hash')
        self.assertEqual(row['estado_manual'],'Apto Clínico')
        self.assertEqual(row['fecha_ultimo_tatuaje'],'2025-01-01')
        self.assertIsNone(row['fecha_ultimo_piercing'])
    def test_validators(self):
        self.assertTrue(ENV['validar_nombre']('María José'))
        for bad in ['Ana1','Ana<script>',''] : self.assertFalse(ENV['validar_nombre'](bad))
        for bad in ['03881234567','388abc1234','5493881234567']:self.assertFalse(ENV['validar_telefono'](bad))
        self.assertTrue(ENV['validar_telefono']('3881234567'))
        for bad in ['49','141','70.5','abc','0700']:
            self.assertTrue(ENV['validar_antecedentes']({'peso_kg':bad}))
        for ok in ['50','70','140']:self.assertEqual(ENV['validar_antecedentes']({'peso_kg':ok}),[])
        self.assertTrue(ENV['validar_antecedentes']({'peso_kg':'70','fecha_ultimo_tatuaje':'2099-01-01'}))
    def test_clinical_approval_still_waits_60_days(self):
        d=self.donor(estado_manual='Apto Clínico')
        day=(datetime.now().date()-timedelta(days=10)).isoformat()
        hist=[{'fecha_donacion':day}]
        self.assertEqual(ENV['calcular_elegibilidad'](d,day,hist)[0],'En espera')
        self.assertFalse(ENV['validar_nueva_donacion'](d,hist,datetime.now().date().isoformat())[0])
    def test_clinical_negative_blocks(self):
        d=self.donor(estado_manual='No Apto Clínico')
        self.assertEqual(ENV['calcular_elegibilidad'](d,None,[])[0],'No Apto Clínico')
    def test_tattoo_wait_and_medication(self):
        d=self.donor(fecha_ultimo_tatuaje=datetime.now().date().isoformat())
        self.assertEqual(ENV['calcular_elegibilidad'](d,None,[])[0],'No Apto Temporal')
        d=self.donor('23456789',toma_medicacion=1)
        self.assertEqual(ENV['calcular_elegibilidad'](d,None,[])[0],'No Apto Temporal')
    def test_campaign_slots(self):
        f=dict(titulo='Campaña',fecha='2099-01-01',horario='08:00 a 12:00',ubicacion='Plaza',localidad='Perico')
        self.assertEqual(ENV['validar_campana'](f)[1],[])
        f['horario']='14:00 a 18:00';self.assertEqual(ENV['validar_campana'](f)[1],[])
        f['horario']='09:00 a 10:00';self.assertTrue(ENV['validar_campana'](f)[1])
    def test_email_queue_is_local_and_idempotent(self):
        self.donor();self.donor('23456789',localidad='Tilcara');self.donor('34567890',estado_manual='No Apto Clínico');self.donor('45678901',toma_medicacion=1)
        cid=self.campaign();ENV['preparar_avisos_campana'](cid);ENV['preparar_avisos_campana'](cid)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM avisos_campana').fetchone()[0],2)
    def test_whatsapp_campaign_filters_and_none(self):
        self.donor();self.donor('23456789',localidad='Tilcara');self.donor('34567890',toma_medicacion=1)
        cid=self.campaign()
        ENV['request']=SimpleNamespace(args=Params(campana_id=str(cid)))
        self.assertEqual(len(ENV['admin_notificaciones']()['donantes']),1)
        ENV['request']=SimpleNamespace(args=Params())
        self.assertEqual(len(ENV['admin_notificaciones']()['donantes']),2)
        ENV['request']=SimpleNamespace(args=Params(campana_id='999'))
        with self.assertRaises(ValueError):ENV['admin_notificaciones']()
    def test_extraction_stats_use_snapshot(self):
        d=self.donor();cid=self.campaign()
        self.db.execute("INSERT INTO donaciones(donante_id,fecha_donacion,grupo_extraccion,rh_extraccion,localidad_extraccion,campana_id) VALUES (?,'2026-01-01','AB','-','Tilcara',?)",(d['id'],cid));self.db.commit()
        out=ENV['admin_estadisticas']()
        self.assertEqual(out['total_extracciones'],1)
        self.assertEqual(out['extracciones'][1][1][0]['etiqueta'],'AB-')
        self.assertEqual(out['extracciones'][3][1][0]['etiqueta'],'Tilcara')
    def test_disk_persistence_across_reconnect(self):
        self.donor();self.db.close()
        ENV['init_db_if_missing']()
        self.db=sqlite3.connect(self.path)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM donantes').fetchone()[0],1)

if __name__=='__main__':unittest.main()
