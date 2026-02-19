from __future__ import annotations

import os
import random
import sqlite3
from faker import Faker

APP_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(APP_DIR, "patients.db")

BLOOD_TYPES = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                age INTEGER NOT NULL,
                blood_type TEXT NOT NULL
            )
            """
        )

def seed(n: int = 100, locale: str = "es_ES", seed_value: int = 20260213) -> None:
    """
    Genera una base DEMO con datos totalmente ficticios.
    - n: cantidad de registros
    - locale: idioma/estilo de nombres (es_ES funciona bien; podés probar es_AR)
    - seed_value: para que siempre se genere la MISMA lista
    """
    random.seed(seed_value)
    fake = Faker(locale)
    fake.seed_instance(seed_value)

    conn = get_conn()
    init_db(conn)

    # Limpia contenido previo
    with conn:
        conn.execute("DELETE FROM patients")

    patients = []
    for _ in range(n):
        name = fake.name()
        age = random.randint(18, 65)
        blood = random.choices(
            BLOOD_TYPES,
            weights=[30, 6, 12, 3, 5, 1, 38, 5],  # distribución aproximada (no clínica)
            k=1
        )[0]
        patients.append((name, age, blood))

    with conn:
        conn.executemany(
            "INSERT INTO patients (name, age, blood_type) VALUES (?, ?, ?)",
            patients
        )

    conn.close()
    print(f"✅ Cargados {n} registros ficticios en {DB_PATH}")

if __name__ == "__main__":
    seed()
