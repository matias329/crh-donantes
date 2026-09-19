"""
Genera datos de demostración para el sistema CRH Jujuy:
  - 200 donantes ficticios distribuidos en localidades de Jujuy.
  - Historial de donaciones simulado (para ver los tres estados clínicos
    en acción: Apto, En espera y No apto).
  - Un puñado de campañas de ejemplo.

Uso:
    python poblar_datos.py
    python poblar_datos.py --cantidad 300 --semilla 7
"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

APP_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(APP_DIR, "patients.db"))

BLOOD_GROUPS = ["A", "B", "AB", "O"]
RH_OPTIONS = ["+", "-"]

# Nombres y apellidos frecuentes en Argentina/Jujuy, combinados al azar para
# generar 200 identidades variadas sin depender de una librería externa de
# nombres (Faker trae un bug conocido en el locale es_AR de esta versión).
NOMBRES_MASCULINOS = [
    "Carlos", "Jorge", "Lucas", "Mateo", "Diego", "Facundo", "Ricardo", "Pedro",
    "Martín", "Nicolás", "Gonzalo", "Emanuel", "Sebastián", "Fernando", "Gustavo",
    "Rodrigo", "Ezequiel", "Ramiro", "Agustín", "Braian", "Cristian", "Damián",
    "Franco", "Hernán", "Ivo", "Julián", "Leandro", "Marcos", "Nahuel", "Octavio",
]
NOMBRES_FEMENINOS = [
    "María Luz", "Sofía", "Camila", "Elena", "Martina", "Laura", "Gabriela",
    "Valentina", "Lucía", "Ana", "Florencia", "Julieta", "Rocío", "Antonella",
    "Milagros", "Carla", "Daniela", "Estefanía", "Guadalupe", "Ivana", "Jazmín",
    "Karen", "Luciana", "Micaela", "Noelia", "Paola", "Romina", "Silvina",
    "Tamara", "Yamila",
]
APELLIDOS = [
    "Albarracín", "Flores", "Maidana", "Tolaba", "Aramayo", "Mamani", "Quispe",
    "Vilte", "Cruz", "Jerez", "Arias", "Soraire", "Calisaya", "Choque", "Tito",
    "Torres", "Cardozo", "Guzmán", "Farfán", "Bustamante", "Salazar", "Cazón",
    "Chocobar", "Colque", "Fernández", "Gutiérrez", "Herrera", "López",
    "Martínez", "Núñez", "Ovando", "Paredes", "Ramos", "Rojas", "Sarapura",
    "Segovia", "Villagrán", "Zambrano",
]

# Localidades de Jujuy con un peso aproximado a su población real,
# para que la distribución de donantes no sea uniforme.
LOCALIDADES_PESO = [
    ("San Salvador de Jujuy", 40),
    ("Palpalá", 15),
    ("Perico", 10),
    ("El Carmen", 10),
    ("Libertador General San Martín", 8),
    ("Humahuaca", 6),
    ("Tilcara", 6),
    ("La Quiaca", 5),
]

# Prefijos telefónicos típicos de la provincia de Jujuy.
PREFIJOS_TELEFONO = ["388", "3886", "3887"]

CONTRASENA_DEMO = "donante123"


def crear_esquema(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS donantes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dni TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL,
                fecha_nacimiento TEXT NOT NULL,
                genero TEXT NOT NULL,
                grupo_sanguineo TEXT NOT NULL,
                factor_rh TEXT NOT NULL,
                localidad TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                telefono TEXT,
                password_hash TEXT NOT NULL,
                estado_manual TEXT DEFAULT 'Automatico',
                observaciones_medicas TEXT DEFAULT '',
                fecha_ultimo_tatuaje TEXT,
                fecha_ultimo_piercing TEXT,
                toma_medicacion BOOLEAN DEFAULT 0,
                peso_kg REAL,
                embarazo BOOLEAN DEFAULT 0
            );
        """)
        columnas_donantes = {fila[1] for fila in conn.execute("PRAGMA table_info(donantes)")}
        if "peso_kg" not in columnas_donantes:
            conn.execute("ALTER TABLE donantes ADD COLUMN peso_kg REAL")
        if "embarazo" not in columnas_donantes:
            conn.execute("ALTER TABLE donantes ADD COLUMN embarazo BOOLEAN DEFAULT 0")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS donaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donante_id INTEGER NOT NULL,
                fecha_donacion TEXT NOT NULL,
                volumen_ml INTEGER,
                observaciones TEXT,
                lugar TEXT NOT NULL DEFAULT 'Centro Regional de Hemoterapia de Jujuy'
            );
        """)
        columnas_donaciones = {fila[1] for fila in conn.execute("PRAGMA table_info(donaciones)")}
        if "lugar" not in columnas_donaciones:
            conn.execute("ALTER TABLE donaciones ADD COLUMN lugar TEXT NOT NULL DEFAULT 'Centro Regional de Hemoterapia de Jujuy'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campanas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                tipo TEXT NOT NULL,
                fecha TEXT NOT NULL,
                ubicacion TEXT NOT NULL,
                localidad TEXT NOT NULL,
                horario TEXT NOT NULL DEFAULT '08:00 a 12:00',
                publicada INTEGER NOT NULL DEFAULT 1
            );
        """)
        columnas_campanas = {fila[1] for fila in conn.execute("PRAGMA table_info(campanas)")}
        if "horario" not in columnas_campanas:
            conn.execute("ALTER TABLE campanas ADD COLUMN horario TEXT NOT NULL DEFAULT '08:00 a 12:00'")
        if "publicada" not in columnas_campanas:
            conn.execute("ALTER TABLE campanas ADD COLUMN publicada INTEGER NOT NULL DEFAULT 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS turnos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donante_id INTEGER NOT NULL,
                campana_id INTEGER NOT NULL,
                hora TEXT NOT NULL,
                UNIQUE(donante_id, campana_id)
            );
        """)


def elegir_localidad(rng: random.Random) -> str:
    localidades, pesos = zip(*LOCALIDADES_PESO)
    return rng.choices(localidades, weights=pesos, k=1)[0]


def fecha_nacimiento_para_edad(rng: random.Random, edad_min: int, edad_max: int) -> date:
    hoy = date.today()
    edad = rng.randint(edad_min, edad_max)
    dia_juliano = rng.randint(1, 365)
    return date(hoy.year - edad, 1, 1) + timedelta(days=dia_juliano - 1)


def generar_email(nombre: str, dni: str, usados: set[str]) -> str:
    base = nombre.lower()
    for viejo, nuevo in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        base = base.replace(viejo, nuevo)
    base = "".join(c for c in base if c.isalnum() or c == " ").strip().replace(" ", ".")
    email = f"{base}@mail.com"
    if email in usados:
        email = f"{base}.{dni[-3:]}@mail.com"
    usados.add(email)
    return email


def generar_nombre(rng: random.Random, genero: str) -> str:
    pool = NOMBRES_MASCULINOS if genero == "M" else NOMBRES_FEMENINOS
    return f"{rng.choice(pool)} {rng.choice(APELLIDOS)}"


def generar_donante(rng: random.Random, dnis_usados: set[str], emails_usados: set[str]) -> dict:
    genero = rng.choice(["M", "F"])
    nombre = generar_nombre(rng, genero)

    # La mayoría de los donantes están dentro del rango legal (18-65); un pequeño
    # porcentaje queda fuera a propósito para ejercitar el caso "No apto por edad".
    if rng.random() < 0.06:
        edad_min, edad_max = 66, 80
    else:
        edad_min, edad_max = 18, 65
    fecha_nac = fecha_nacimiento_para_edad(rng, edad_min, edad_max)

    dni = str(rng.randint(20_000_000, 47_000_000))
    while dni in dnis_usados:
        dni = str(rng.randint(20_000_000, 47_000_000))
    dnis_usados.add(dni)

    # ~10% con un tatuaje o piercing reciente (menos de 12 meses) -> inhabilita temporalmente.
    fecha_tatuaje = None
    fecha_piercing = None
    if rng.random() < 0.10:
        dias_atras = rng.randint(10, 340)
        fecha_tatuaje = (date.today() - timedelta(days=dias_atras)).isoformat()
    elif rng.random() < 0.05:
        dias_atras = rng.randint(10, 340)
        fecha_piercing = (date.today() - timedelta(days=dias_atras)).isoformat()

    toma_medicacion = 1 if rng.random() < 0.07 else 0
    peso_kg = round(rng.uniform(48, 105), 1)
    embarazo = 1 if genero == "F" and rng.random() < 0.03 else 0

    # Un pequeño grupo con decisión médica manual, para reflejar casos reales de excepción.
    estado_manual, observaciones = "Automatico", ""
    dado = rng.random()
    if dado < 0.02:
        estado_manual, observaciones = "No Apto Manual", "Rechazado temporalmente por indicación médica."
    elif dado < 0.035:
        estado_manual, observaciones = "Apto Manual", "Alta médica otorgada por el Director de Hemoterapia."

    return {
        "dni": dni,
        "nombre": nombre,
        "fecha_nacimiento": fecha_nac.isoformat(),
        "genero": genero,
        "grupo_sanguineo": rng.choice(BLOOD_GROUPS),
        "factor_rh": rng.choices(RH_OPTIONS, weights=[85, 15], k=1)[0],
        "localidad": elegir_localidad(rng),
        "email": generar_email(nombre, dni, emails_usados),
        "telefono": f"{rng.choice(PREFIJOS_TELEFONO)}{rng.randint(4_000_000, 7_999_999)}",
        "estado_manual": estado_manual,
        "observaciones_medicas": observaciones,
        "fecha_ultimo_tatuaje": fecha_tatuaje,
        "fecha_ultimo_piercing": fecha_piercing,
        "toma_medicacion": toma_medicacion,
        "peso_kg": peso_kg,
        "embarazo": embarazo,
    }


def poblar_donantes(conn: sqlite3.Connection, cantidad: int, rng: random.Random) -> list[int]:
    conn.execute("DELETE FROM turnos")
    conn.execute("DELETE FROM donaciones")
    conn.execute("DELETE FROM donantes")

    password_hash = generate_password_hash(CONTRASENA_DEMO)
    dnis_usados: set[str] = set()
    emails_usados: set[str] = set()
    ids_insertados = []

    with conn:
        for _ in range(cantidad):
            d = generar_donante(rng, dnis_usados, emails_usados)
            cur = conn.execute(
                """INSERT INTO donantes
                   (dni, nombre, fecha_nacimiento, genero, grupo_sanguineo, factor_rh, localidad,
                    email, telefono, password_hash, estado_manual, observaciones_medicas,
                    fecha_ultimo_tatuaje, fecha_ultimo_piercing, toma_medicacion, peso_kg, embarazo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    d["dni"], d["nombre"], d["fecha_nacimiento"], d["genero"], d["grupo_sanguineo"],
                    d["factor_rh"], d["localidad"], d["email"], d["telefono"], password_hash,
                    d["estado_manual"], d["observaciones_medicas"],
                    d["fecha_ultimo_tatuaje"], d["fecha_ultimo_piercing"], d["toma_medicacion"],
                    d["peso_kg"], d["embarazo"],
                ),
            )
            ids_insertados.append((cur.lastrowid, d["genero"]))

    return ids_insertados


def poblar_donaciones(conn: sqlite3.Connection, donantes: list[tuple[int, str]], rng: random.Random) -> None:
    """Genera historial de donación para ~55% de los donantes.

    A propósito, una porción de esas donaciones cae dentro de la ventana
    sanitaria (90/120 días) para que el estado "En espera" también se vea
    reflejado en la demo.
    """
    hoy = date.today()
    with conn:
        for donante_id, genero in donantes:
            if rng.random() > 0.55:
                continue
            if rng.random() < 0.3:
                dias_atras = rng.randint(5, 80)  # cae en ventana -> "En espera"
            else:
                dias_atras = rng.randint(130, 500)  # ya pasó la ventana -> "Apto"
            fecha = (hoy - timedelta(days=dias_atras)).isoformat()
            conn.execute(
                """INSERT INTO donaciones (donante_id, fecha_donacion, volumen_ml, observaciones, lugar)
                   VALUES (?, ?, ?, ?, ?)""",
                (donante_id, fecha, rng.choice([420, 450, 460, 470]), "Donación registrada en campaña CRH", "Centro Regional de Hemoterapia de Jujuy"),
            )


def poblar_campanas(conn: sqlite3.Connection) -> None:
    hoy = date.today()
    campanas = [
        ("Gran Colecta de Invierno", "Colecta de Sangre", hoy + timedelta(days=10),
         "Plaza Belgrano - Carpa Sanitaria", "San Salvador de Jujuy"),
        ("Colecta de Reposición", "Colecta de Sangre", hoy + timedelta(days=18),
         "Hospital Wenceslao Gallardo", "Palpalá"),
        ("Jornada Sanitaria Comunitaria", "Vacunación", hoy + timedelta(days=24),
         "Plaza Central de Perico", "Perico"),
        ("Colecta Externa El Carmen", "Colecta de Sangre", hoy + timedelta(days=32),
         "Centro de Salud N°1", "El Carmen"),
        ("Control y Donación en Quebrada", "Colecta de Sangre", hoy + timedelta(days=40),
         "Posta Sanitaria Humahuaca", "Humahuaca"),
        ("Campaña de Vacunación Antigripal", "Vacunación", hoy + timedelta(days=48),
         "Hospital de Tilcara", "Tilcara"),
    ]
    with conn:
        conn.execute("DELETE FROM campanas")
        for titulo, tipo, fecha, ubicacion, localidad in campanas:
            conn.execute(
                "INSERT INTO campanas (titulo, tipo, fecha, ubicacion, localidad, horario, publicada) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (titulo, tipo, fecha.isoformat(), ubicacion, localidad, "08:00 a 12:00"),
            )


def poblar_sistema(cantidad: int = 200, semilla: int = 2026, confirmar_borrado: bool = False) -> None:
    if os.path.exists(DB_PATH) and not confirmar_borrado:
        raise RuntimeError(
            f"La base {DB_PATH} ya existe. Para reemplazarla conscientemente usá --confirmar-borrado."
        )
    rng = random.Random(semilla)
    conn = sqlite3.connect(DB_PATH)
    try:
        crear_esquema(conn)
        print(f"Generando {cantidad} donantes ficticios de Jujuy...")
        donantes = poblar_donantes(conn, cantidad, rng)
        print("Simulando historial de donaciones...")
        poblar_donaciones(conn, donantes, rng)
        print("Cargando campañas de ejemplo...")
        poblar_campanas(conn)
        print(f"¡Listo! {cantidad} donantes, historial clínico y campañas cargados.")
        print(f"Contraseña de demo para todos los donantes: {CONTRASENA_DEMO}")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poblar CRH Jujuy con datos ficticios.")
    parser.add_argument("--cantidad", type=int, default=200, help="Cantidad de donantes a generar.")
    parser.add_argument("--semilla", type=int, default=2026, help="Semilla para resultados reproducibles.")
    parser.add_argument("--confirmar-borrado", action="store_true", help="Permite reemplazar datos existentes de la base demo.")
    args = parser.parse_args()
    poblar_sistema(cantidad=args.cantidad, semilla=args.semilla, confirmar_borrado=args.confirmar_borrado)
