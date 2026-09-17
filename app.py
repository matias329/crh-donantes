from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# Configuración general
# ---------------------------------------------------------------------------
APP_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(APP_DIR, "patients.db")

BLOOD_GROUPS = ["A", "B", "AB", "O"]
RH_OPTIONS = ["+", "-"]
GENDERS = ["M", "F"]

LOCALIDADES = [
    "San Salvador de Jujuy", "Palpalá", "Perico", "El Carmen",
    "Libertador", "Humahuaca", "Tilcara", "Otra",
]

# Ventanas sanitarias mínimas entre donaciones, en días, según género.
VENTANA_DIAS = {"M": 90, "F": 120}
EDAD_MINIMA = 18
EDAD_MAXIMA = 65
DONANTES_POR_PAGINA = 15

ADMIN_USER = os.environ.get("ADMIN_USER", "admin@crh.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-sanitario-crh")


# ---------------------------------------------------------------------------
# Conexión a la base de datos (una por request, reutilizable con `g`)
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def cerrar_db(exception=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db_if_missing() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
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
                    toma_medicacion BOOLEAN DEFAULT 0
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS donaciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    donante_id INTEGER NOT NULL,
                    fecha_donacion TEXT NOT NULL,
                    volumen_ml INTEGER,
                    observaciones TEXT
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS campanas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    titulo TEXT NOT NULL,
                    tipo TEXT NOT NULL,
                    fecha TEXT NOT NULL,
                    ubicacion TEXT NOT NULL,
                    localidad TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS turnos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    donante_id INTEGER NOT NULL,
                    campana_id INTEGER NOT NULL,
                    hora TEXT NOT NULL,
                    UNIQUE(donante_id, campana_id)
                );
            """)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Utilidades clínicas (edad y elegibilidad)
# ---------------------------------------------------------------------------
def calcular_edad(fecha_nacimiento: str, referencia: Optional[datetime] = None) -> Optional[int]:
    """Devuelve la edad en años a partir de una fecha 'YYYY-MM-DD', o None si es inválida."""
    hoy = (referencia or datetime.now()).date()
    try:
        f_nac = datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return hoy.year - f_nac.year - ((hoy.month, hoy.day) < (f_nac.month, f_nac.day))


def calcular_elegibilidad(donante: sqlite3.Row, ultima_donacion_str: Optional[str]) -> tuple[str, str]:
    """Determina el estado clínico del donante: Apto, En espera o No apto.

    Si hay una decisión médica manual cargada, esa decisión tiene prioridad
    sobre el cálculo automático.
    """
    if donante["estado_manual"] != "Automatico":
        return donante["estado_manual"], donante["observaciones_medicas"] or "Decisión médica manual."

    edad = calcular_edad(donante["fecha_nacimiento"])
    if edad is None:
        return "No apto", "Fecha de nacimiento inválida."
    if edad < EDAD_MINIMA or edad > EDAD_MAXIMA:
        return "No apto", f"Edad no permitida ({edad} años). Rango legal: {EDAD_MINIMA}-{EDAD_MAXIMA}."

    hoy = datetime.now().date()
    for campo in ("fecha_ultimo_tatuaje", "fecha_ultimo_piercing"):
        valor = donante[campo] if campo in donante.keys() else None
        if not valor:
            continue
        try:
            fecha = datetime.strptime(valor, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (hoy - fecha).days < 365:
            return "No apto", "Inhabilitación temporal: debe esperar 12 meses desde su último tatuaje/piercing."

    if donante["toma_medicacion"]:
        return "No apto", "Inhabilitación temporal: requiere evaluación médica presencial."

    if ultima_donacion_str:
        try:
            ultima_don = datetime.strptime(ultima_donacion_str, "%Y-%m-%d").date()
            intervalo = VENTANA_DIAS.get(donante["genero"], 90)
            fecha_regreso = ultima_don + timedelta(days=intervalo)
            if hoy < fecha_regreso:
                dias_restantes = (fecha_regreso - hoy).days
                return "En espera", f"Faltan {dias_restantes} días. Podrás volver a donar el {fecha_regreso.strftime('%d/%m/%Y')}."
        except ValueError:
            pass

    return "Apto", "El donante cumple con los requisitos legales actuales."


def obtener_historial(donante_id: int) -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM donaciones WHERE donante_id = ? ORDER BY fecha_donacion DESC",
        (donante_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Filtro de fecha reutilizable en las plantillas
# ---------------------------------------------------------------------------
@app.template_filter("fecha_legible")
def fecha_legible(valor: str) -> str:
    try:
        return datetime.strptime(valor, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return valor or "—"


# ---------------------------------------------------------------------------
# Páginas públicas
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    db = get_db()
    total_donantes = db.execute("SELECT COUNT(*) FROM donantes").fetchone()[0]
    total_donaciones = db.execute("SELECT COUNT(*) FROM donaciones").fetchone()[0]
    campanas = db.execute(
        "SELECT * FROM campanas WHERE fecha >= date('now') ORDER BY fecha ASC LIMIT 6"
    ).fetchall()
    campanas = [dict(c, fecha_legible=fecha_legible(c["fecha"])) for c in campanas]
    return render_template(
        "landing.html",
        total_donantes=total_donantes,
        total_donaciones=total_donaciones,
        campanas=campanas,
    )


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        db = get_db()
        f_tat = request.form.get("fecha_ultimo_tatuaje") or None
        f_pier = request.form.get("fecha_ultimo_piercing") or None
        toma_medicacion = 1 if request.form.get("toma_medicacion") else 0
        try:
            db.execute(
                """INSERT INTO donantes
                   (dni, nombre, fecha_nacimiento, genero, grupo_sanguineo, factor_rh,
                    localidad, email, telefono, password_hash,
                    fecha_ultimo_tatuaje, fecha_ultimo_piercing, toma_medicacion)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request.form["dni"], request.form["nombre"], request.form["fecha_nacimiento"],
                    request.form["genero"], request.form["grupo_sanguineo"], request.form["factor_rh"],
                    request.form["localidad"], request.form["email"], request.form["telefono"],
                    generate_password_hash(request.form["password"]),
                    f_tat, f_pier, toma_medicacion,
                ),
            )
            db.commit()
            flash("Registro exitoso. Ya podés iniciar sesión.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Error: el DNI o el correo ya están registrados.", "warning")
    return render_template(
        "registro.html", localidades=LOCALIDADES, blood_groups=BLOOD_GROUPS, rh_options=RH_OPTIONS
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        res = get_db().execute("SELECT * FROM donantes WHERE email = ?", (email,)).fetchone()
        if res and check_password_hash(res["password_hash"], password):
            session["user_id"] = res["id"]
            session["user_name"] = res["nombre"]
            return redirect(url_for("perfil_donante"))
        flash("Credenciales incorrectas.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Portal del donante
# ---------------------------------------------------------------------------
@app.route("/mi-portal")
def perfil_donante():
    if "user_id" not in session:
        return redirect(url_for("login"))
    db = get_db()
    donante = db.execute("SELECT * FROM donantes WHERE id = ?", (session["user_id"],)).fetchone()
    if donante is None:
        session.clear()
        return redirect(url_for("login"))

    historial = obtener_historial(donante["id"])
    ultima_donacion_str = historial[0]["fecha_donacion"] if historial else None
    estado, motivo = calcular_elegibilidad(donante, ultima_donacion_str)

    campanas = db.execute(
        "SELECT * FROM campanas WHERE localidad = ? AND fecha >= date('now') ORDER BY fecha ASC",
        (donante["localidad"],),
    ).fetchall()
    turnos = db.execute(
        """SELECT t.hora, c.titulo FROM turnos t
           JOIN campanas c ON t.campana_id = c.id
           WHERE t.donante_id = ?""",
        (donante["id"],),
    ).fetchall()

    return render_template(
        "portal_donante.html",
        donante=donante,
        edad=calcular_edad(donante["fecha_nacimiento"]),
        estado=estado,
        motivo=motivo,
        campanas=campanas,
        historial=historial,
        turnos=turnos,
    )


@app.route("/reservar-turno", methods=["POST"])
def reservar_turno():
    if "user_id" not in session:
        return redirect(url_for("login"))
    db = get_db()
    try:
        db.execute(
            "INSERT INTO turnos (donante_id, campana_id, hora) VALUES (?, ?, ?)",
            (session["user_id"], request.form.get("campana_id"), request.form.get("hora")),
        )
        db.commit()
        flash("Turno reservado con éxito.", "success")
    except sqlite3.IntegrityError:
        flash("Ya tenés un turno reservado en esa campaña.", "warning")
    return redirect(url_for("perfil_donante"))


# ---------------------------------------------------------------------------
# Panel administrativo
# ---------------------------------------------------------------------------
def admin_requerido():
    return session.get("admin_logged", False)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        usuario = request.form.get("username", "")
        clave = request.form.get("password", "")
        if usuario == ADMIN_USER and clave == ADMIN_PASSWORD:
            session["admin_logged"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Credenciales incorrectas.", "danger")
    return render_template("admin_login.html")


@app.route("/admin")
def admin_dashboard():
    if not admin_requerido():
        return redirect(url_for("admin_login"))

    db = get_db()
    q = request.args.get("q", "").strip()
    grupo = request.args.get("grupo", "").strip()
    localidad = request.args.get("localidad", "").strip()
    edad_min = request.args.get("edad_min", "").strip()
    edad_max = request.args.get("edad_max", "").strip()
    pagina = max(1, request.args.get("pagina", 1, type=int))

    condiciones, parametros = [], []
    if q:
        condiciones.append("(nombre LIKE ? OR dni LIKE ?)")
        parametros += [f"%{q}%", f"%{q}%"]
    if grupo:
        condiciones.append("grupo_sanguineo = ?")
        parametros.append(grupo)
    if localidad:
        condiciones.append("localidad = ?")
        parametros.append(localidad)

    where_sql = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    filas = db.execute(f"SELECT * FROM donantes {where_sql} ORDER BY id DESC", parametros).fetchall()

    # El rango de edad se filtra en memoria porque la edad se deriva de la fecha de nacimiento.
    resultado = []
    for fila in filas:
        edad = calcular_edad(fila["fecha_nacimiento"]) or 0
        if edad_min and edad < int(edad_min):
            continue
        if edad_max and edad > int(edad_max):
            continue
        historial = obtener_historial(fila["id"])
        ultima = historial[0]["fecha_donacion"] if historial else None
        estado, _ = calcular_elegibilidad(fila, ultima)
        resultado.append({**dict(fila), "edad": edad, "estado_calculado": estado})

    total = len(resultado)
    total_paginas = max(1, (total + DONANTES_POR_PAGINA - 1) // DONANTES_POR_PAGINA)
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * DONANTES_POR_PAGINA
    donantes_pagina = resultado[inicio:inicio + DONANTES_POR_PAGINA]

    campanas = db.execute("SELECT * FROM campanas ORDER BY fecha DESC").fetchall()

    return render_template(
        "admin_dashboard.html",
        donantes=donantes_pagina,
        campanas=campanas,
        total=total,
        pagina=pagina,
        total_paginas=total_paginas,
        blood_groups=BLOOD_GROUPS,
        localidades=LOCALIDADES,
        filtros_activos=bool(q or grupo or localidad or edad_min or edad_max),
    )


@app.route("/admin/paciente/<int:id>", methods=["GET", "POST"])
def admin_detalle_paciente(id):
    if not admin_requerido():
        return redirect(url_for("admin_login"))

    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_status":
            db.execute(
                "UPDATE donantes SET estado_manual = ?, observaciones_medicas = ? WHERE id = ?",
                (request.form.get("estado_manual"), request.form.get("observaciones_medicas"), id),
            )
            db.commit()
            flash("Estado actualizado.", "success")
        elif action == "add_donation":
            db.execute(
                "INSERT INTO donaciones (donante_id, fecha_donacion, volumen_ml, observaciones) VALUES (?, ?, ?, ?)",
                (id, request.form.get("fecha_donacion"), request.form.get("volumen_ml") or None,
                 request.form.get("observaciones")),
            )
            db.execute("UPDATE donantes SET estado_manual = 'Automatico' WHERE id = ?", (id,))
            db.commit()
            flash("Donación registrada y estado recalculado.", "success")

    d = db.execute("SELECT * FROM donantes WHERE id = ?", (id,)).fetchone()
    if d is None:
        flash("Donante no encontrado.", "warning")
        return redirect(url_for("admin_dashboard"))

    historial = obtener_historial(id)
    ultima_donacion_str = historial[0]["fecha_donacion"] if historial else None
    estado, motivo = calcular_elegibilidad(d, ultima_donacion_str)

    return render_template("admin_detalle.html", d=d, historial=historial, estado=estado, motivo=motivo)


init_db_if_missing()

if __name__ == "__main__":
    app.run(debug=True)
