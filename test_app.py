import os
import tempfile
import unittest


class AppSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(cls.db_path)
        os.environ["DB_PATH"] = cls.db_path
        os.environ["FLASK_SECRET_KEY"] = "clave-segura-de-prueba-no-productiva"
        import app
        cls.mod = app
        cls.client = app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            os.unlink(cls.db_path)

    def token(self, ruta):
        self.client.get(ruta)
        with self.client.session_transaction() as sesion:
            return sesion["csrf_token"]

    def test_paginas_publicas(self):
        for ruta in ("/", "/registro", "/login", "/admin/login"):
            self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_post_sin_csrf_es_rechazado(self):
        self.assertEqual(self.client.post("/login", data={}).status_code, 400)

    def test_login_admin_con_csrf(self):
        token = self.token("/admin/login")
        respuesta = self.client.post("/admin/login", data={
            "csrf_token": token,
            "username": "admin@crh.com",
            "password": "admin123",
        })
        self.assertEqual(respuesta.status_code, 302)

    def login_admin(self):
        token = self.token("/admin/login")
        return self.client.post("/admin/login", data={"csrf_token": token, "username": "admin@crh.com", "password": "admin123"})

    def test_crud_campana_y_estadisticas(self):
        self.login_admin()
        token = self.token("/admin/campanas/nueva")
        respuesta = self.client.post("/admin/campanas/nueva", data={
            "csrf_token": token, "titulo": "Colecta de prueba", "fecha": "2099-12-10",
            "horario": "08:00 a 12:00", "ubicacion": "Dirección de prueba",
            "localidad": "San Salvador de Jujuy", "publicada": "1",
        })
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.client.get("/admin/estadisticas").status_code, 200)

    def test_pdf_requiere_autorizacion(self):
        self.assertIn(self.client.get("/constancia/999.pdf").status_code, (403, 404))


if __name__ == "__main__":
    unittest.main()
