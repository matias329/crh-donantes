from __future__ import annotations

import os
import sqlite3
from typing import Any, Iterable, Optional

from flask import Flask, render_template, request, redirect, url_for, flash

APP_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(APP_DIR, "patients.db")  # SQLite local

BLOOD_GROUPS = ["A", "B", "AB", "O"]
RH_OPTIONS = ["+", "-", "NS"]  # NS = No sé
GENDERS = ["M", "F"]

LOCALIDADES = [
    "San Salvador de Jujuy", "Palpalá", "Perico", "El Carmen",
    "Libertador", "Humahuaca", "Tilcara", "Otra"
]

ESTADOS = ["activo", "pendiente_validacion"]

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")


def is_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def normalize_database_url(url: str) -> str:
    # Render a veces da postgres:// y psycopg2 prefiere postgresql://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def get_db_connection():
    """
    Devuelve una conexión:
    - PostgreSQL si existe DATABASE_URL
    - SQLite si no existe (modo local)
    """
    if is_postgres():
        import psycopg2
        from psycopg2.extras import RealDictCursor

        db_url = normalize_database_url(os.environ["DATABASE_URL"])
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        return conn

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ph() -> str:
    """Placeholder según motor."""
    return "%s" if is_postgres() else "?"


def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    conn = get_db_connection()
    if is_postgres():
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    else:
        rows = conn.execute(sql, tuple(params)).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
    conn = get_db_connection()
    if is_postgres():
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None
    else:
        row = conn.execute(sql, tuple(params)).fetchone()
        conn.close()
        return dict(row) if row else None


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    conn = get_db_connection()
    if is_postgres():
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
        cur.close()
        conn.close()
    else:
        with conn:
            conn.execute(sql, tuple(params))
        conn.close()


def init_db_if_missing() -> None:
    """
    Crea la tabla donantes.
    - En Postgres: crea tabla (sin migración patients)
    - En SQLite: crea tabla + (opcional) migra desde patients si donantes está vacía
    """
    if is_postgres():
        # Tabla en Postgres
        execute(
            """
            CREATE TABLE IF NOT EXISTS donantes (
                id SERIAL PRIMARY KEY,
                dni TEXT NOT NULL UNIQUE,
                nombre TEXT NOT NULL,
                fecha_nacimiento TEXT NOT NULL,      -- YYYY-MM-DD
                genero TEXT NOT NULL,                -- M/F
                grupo_sanguineo TEXT NOT NULL,       -- A/B/AB/O
                factor_rh TEXT NOT NULL,             -- + / - / NS
                estado_validacion TEXT NOT NULL,     -- activo / pendiente_validacion
                localidad TEXT NOT NULL,
                email TEXT NOT NULL,
                telefono TEXT NOT NULL,
                password TEXT NOT NULL               -- demo académica (no productivo)
            );
            """
        )
        return

    # ----- SQLite local -----
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS donantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dni TEXT NOT NULL UNIQUE,
            nombre TEXT NOT NULL,
            fecha_nacimiento TEXT NOT NULL,      -- YYYY-MM-DD
            genero TEXT NOT NULL,                -- M/F
            grupo_sanguineo TEXT NOT NULL,       -- A/B/AB/O
            factor_rh TEXT NOT NULL,             -- + / - / NS
            estado_validacion TEXT NOT NULL,     -- activo / pendiente_validacion
            localidad TEXT NOT NULL,
            email TEXT NOT NULL,
            telefono TEXT NOT NULL,
            password TEXT NOT NULL               -- demo académica (no productivo)
        )
        """
    )

    # Compatibilidad: si ya existía sin "nombre"
    try:
        cur.execute("ALTER TABLE donantes ADD COLUMN nombre TEXT")
        cur.execute("UPDATE donantes SET nombre='Sin nombre' WHERE nombre IS NULL OR TRIM(nombre)=''")
    except sqlite3.OperationalError:
        pass

    # Migración desde tabla vieja patients SOLO si donantes está vacía
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='patients'")
    exists_patients = cur.fetchone() is not None

    cur.execute("SELECT COUNT(*) FROM donantes")
    count_donantes = cur.fetchone()[0]

    if exists_patients and count_donantes == 0:
        rows = conn.execute("SELECT id, name, age, blood_type FROM patients ORDER BY id ASC").fetchall()
        for r in rows:
            fake_dni = f"LEGACY-{r['id']}"
            nombre = (r["name"] or "Sin nombre").strip()
            fecha = "2000-01-01"
            genero = "M"

            bt = (r["blood_type"] or "O+").strip()
            grupo = bt[:-1] if len(bt) >= 2 else "O"
            rh = bt[-1] if bt[-1] in ["+", "-"] else "+"

            estado = "activo"
            localidad = "Otra"
            email = f"legacy{r['id']}@demo.local"
            telefono = "0000000000"
            password = "demo"

            try:
                conn.execute(
                    """
                    INSERT INTO donantes
                    (dni, nombre, fecha_nacimiento, genero, grupo_sanguineo, factor_rh, estado_validacion,
                     localidad, email, telefono, password)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (fake_dni, nombre, fecha, genero, grupo, rh, estado, localidad, email, telefono, password),
                )
            except sqlite3.IntegrityError:
                pass

    conn.commit()
    conn.close()


def calcular_edad(fecha_yyyy_mm_dd: str) -> int | None:
    try:
        y, m, d = fecha_yyyy_mm_dd.split("-")
        y = int(y); m = int(m); d = int(d)
    except Exception:
        return None

    import datetime as _dt
    hoy = _dt.date.today()
    try:
        nac = _dt.date(y, m, d)
    except Exception:
        return None

    return hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))


def validar_dni(dni: str) -> bool:
    return dni.isdigit() and 7 <= len(dni) <= 10


# ✅ LANDING
@app.route("/", methods=["GET"])
def index():
    return render_template("landing.html")


# ✅ LISTADO + BUSCADOR
@app.route("/donantes", methods=["GET"])
def donantes():
    init_db_if_missing()

    q_dni = (request.args.get("dni") or "").strip()
    q_nombre = (request.args.get("nombre") or "").strip()
    q_localidad = (request.args.get("localidad") or "").strip()
    q_grupo = (request.args.get("grupo") or "").strip()
    q_rh = (request.args.get("rh") or "").strip()
    q_estado = (request.args.get("estado") or "").strip()

    where = []
    params: list[Any] = []

    if q_dni:
        where.append(f"dni LIKE {ph()}")
        params.append(f"%{q_dni}%")

    if q_nombre:
        where.append(f"LOWER(nombre) LIKE {ph()}")
        params.append(f"%{q_nombre.lower()}%")

    if q_localidad:
        where.append(f"localidad = {ph()}")
        params.append(q_localidad)

    if q_grupo:
        where.append(f"grupo_sanguineo = {ph()}")
        params.append(q_grupo)

    if q_rh:
        where.append(f"factor_rh = {ph()}")
        params.append(q_rh)

    if q_estado:
        where.append(f"estado_validacion = {ph()}")
        params.append(q_estado)

    sql = """
        SELECT id, dni, nombre, fecha_nacimiento, genero, grupo_sanguineo, factor_rh,
               estado_validacion, localidad, email, telefono
        FROM donantes
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"

    rows = fetch_all(sql, params)
    total_row = fetch_one("SELECT COUNT(*) AS c FROM donantes")
    total = int(total_row["c"]) if total_row else 0

    return render_template(
        "index.html",
        rows=rows,
        total=total,
        localidades=LOCALIDADES,
        blood_groups=BLOOD_GROUPS,
        rh_options=RH_OPTIONS,
        q_dni=q_dni,
        q_nombre=q_nombre,
        q_localidad=q_localidad,
        q_grupo=q_grupo,
        q_rh=q_rh,
        q_estado=q_estado,
    )


# ✅ FICHA / DETALLE DEL DONANTE
@app.route("/donantes/<int:donante_id>", methods=["GET"])
def donante_detalle(donante_id: int):
    init_db_if_missing()

    d = fetch_one(
        f"""
        SELECT id, dni, nombre, fecha_nacimiento, genero,
               grupo_sanguineo, factor_rh, estado_validacion,
               localidad, email, telefono
        FROM donantes
        WHERE id = {ph()}
        """,
        (donante_id,),
    )

    if d is None:
        flash("Donante no encontrado.", "warning")
        return redirect(url_for("donantes"))

    return render_template("donante_detalle.html", d=d)


# ✅ CARGAR DONANTE
@app.route("/donantes/nuevo", methods=["GET", "POST"])
def donantes_nuevo():
    init_db_if_missing()

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        dni = (request.form.get("dni") or "").strip()
        fecha_nacimiento = (request.form.get("fecha_nacimiento") or "").strip()
        genero = (request.form.get("genero") or "").strip()
        grupo = (request.form.get("grupo_sanguineo") or "").strip()
        rh = (request.form.get("factor_rh") or "").strip()
        localidad = (request.form.get("localidad") or "").strip()
        email = (request.form.get("email") or "").strip()
        telefono = (request.form.get("telefono") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not nombre:
            flash("El nombre es obligatorio.", "warning")
            return redirect(url_for("donantes_nuevo"))

        if not validar_dni(dni):
            flash("DNI inválido. Usá solo números (7 a 10 dígitos).", "warning")
            return redirect(url_for("donantes_nuevo"))

        edad = calcular_edad(fecha_nacimiento)
        if edad is None:
            flash("Fecha de nacimiento inválida.", "warning")
            return redirect(url_for("donantes_nuevo"))
        if edad < 18 or edad > 65:
            flash("Edad fuera de rango: solo 18 a 65 años.", "warning")
            return redirect(url_for("donantes_nuevo"))

        if genero not in GENDERS:
            flash("Género inválido.", "warning")
            return redirect(url_for("donantes_nuevo"))

        if grupo not in BLOOD_GROUPS:
            flash("Grupo sanguíneo inválido.", "warning")
            return redirect(url_for("donantes_nuevo"))

        if rh not in RH_OPTIONS:
            flash("Factor RH inválido.", "warning")
            return redirect(url_for("donantes_nuevo"))

        if localidad not in LOCALIDADES:
            flash("Localidad inválida.", "warning")
            return redirect(url_for("donantes_nuevo"))

        if not email or "@" not in email:
            flash("Email inválido.", "warning")
            return redirect(url_for("donantes_nuevo"))

        if not telefono:
            flash("Teléfono/WhatsApp es obligatorio.", "warning")
            return redirect(url_for("donantes_nuevo"))

        if len(password) < 6:
            flash("La contraseña debe tener al menos 6 caracteres.", "warning")
            return redirect(url_for("donantes_nuevo"))

        estado_validacion = "pendiente_validacion" if rh == "NS" else "activo"

        # Insert (con placeholder compatible)
        try:
            execute(
                f"""
                INSERT INTO donantes
                (dni, nombre, fecha_nacimiento, genero, grupo_sanguineo, factor_rh, estado_validacion,
                 localidad, email, telefono, password)
                VALUES ({ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()}, {ph()})
                """,
                (dni, nombre, fecha_nacimiento, genero, grupo, rh, estado_validacion, localidad, email, telefono, password),
            )
        except Exception as e:
            # Si DNI repetido en Postgres o SQLite
            msg = str(e).lower()
            if "unique" in msg or "duplicate" in msg:
                flash("Ese DNI ya está registrado.", "warning")
            else:
                flash("Error al guardar el donante.", "warning")
            return redirect(url_for("donantes_nuevo"))

        flash("Donante registrado correctamente.", "success")
        return redirect(url_for("donantes"))

    return render_template(
        "new.html",
        localidades=LOCALIDADES,
        blood_groups=BLOOD_GROUPS,
        rh_options=RH_OPTIONS,
    )


# ✅ Reset DB (demo)
@app.route("/reset-db", methods=["POST"])
def reset_db():
    if is_postgres():
        # En Postgres borramos tabla y la recreamos
        execute("DROP TABLE IF EXISTS donantes;")
        init_db_if_missing()
        flash("Base reiniciada (PostgreSQL).", "info")
        return redirect(url_for("donantes"))

    # SQLite local
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db_if_missing()
    flash("Base reiniciada (SQLite).", "info")
    return redirect(url_for("donantes"))


if __name__ == "__main__":
    # Local: python app.py
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
