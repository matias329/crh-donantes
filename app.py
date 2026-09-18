from __future__ import annotations

import functools
import hmac
import os
import re
import secrets
import smtplib
import sqlite3
import time
from io import BytesIO
from urllib.parse import quote
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Optional

from flask import Flask, render_template, request, redirect, url_for, flash, session, g, send_file, abort
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

# ---------------------------------------------------------------------------
# Configuración general
# ---------------------------------------------------------------------------
APP_DIR = os.path.abspath(os.path.dirname(__file__))
# Se puede sobreescribir por variable de entorno para apuntar a un disco
# persistente (p. ej. en Render/Heroku) y evitar que la base se pierda
# cada vez que el proceso se reinicia o se redeploya la app.
DB_PATH = os.environ.get("DB_PATH", os.path.join(APP_DIR, "patients.db"))

# Tiempo de validez (en segundos) del enlace de recuperación de contraseña.
RESET_TOKEN_MAX_AGE = 60 * 60  # 1 hora

# Configuración SMTP opcional para el envío real de emails. Si no está
# configurada, el sistema igualmente genera el enlace de recuperación y lo
# muestra/registra para no bloquear el flujo en entornos de desarrollo.
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM = os.environ.get("SMTP_FROM", "no-responder@crhjujuy.org")

BLOOD_GROUPS = ["A", "B", "AB", "O", "No sé"]
RH_OPTIONS = ["+", "-", "No sé"]
GENDERS = ["M", "F"]

LOCALIDADES = [
    "San Salvador de Jujuy", "Palpalá", "Perico", "El Carmen",
    "Libertador", "Humahuaca", "Tilcara", "Otra",
]
ZONAS = {
    "Valles": ["San Salvador de Jujuy", "Palpalá", "Perico", "El Carmen"],
    "Yungas y Ramal": ["Libertador"],
    "Quebrada": ["Humahuaca", "Tilcara"],
    "Otras": ["Otra"],
}

ESTADOS_AYUDA = {
    "Automatico": "Estado evaluado automáticamente según edad, antecedentes declarados y última donación.",
    "Apto": "Preclasificación automática favorable. La aptitud definitiva la confirma el equipo profesional.",
    "Apto Manual": "Un profesional registró manualmente una condición favorable.",
    "No apto": "La evaluación automática detectó una condición que requiere espera o revisión profesional.",
    "No Apto Manual": "Un profesional indicó que la persona no está habilitada hasta una nueva evaluación.",
    "En espera": "Todavía no se cumplió el intervalo requerido desde la última donación.",
}

# Preselección basada en criterios nacionales publicados. La aptitud final
# siempre corresponde al equipo profesional en la entrevista presencial.
VENTANA_DIAS = {"M": 60, "F": 60}
MAX_DONACIONES_ANUALES = {"M": 4, "F": 3}
EDAD_MINIMA = 18
EDAD_MAXIMA = 65
DONANTES_POR_PAGINA = 15

ADMIN_USER = os.environ.get("ADMIN_USER", "admin@crh.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
APP_ENV = os.environ.get("APP_ENV", "development").lower()
ALLOW_DEMO_RESET_LINK = os.environ.get("ALLOW_DEMO_RESET_LINK", "false").lower() == "true"
WHATSAPP_CONSULTAS = re.sub(r"\D", "", os.environ.get("WHATSAPP_CONSULTAS", "+54 9 388 755-7004"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-sanitario-crh")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=APP_ENV == "production",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)

if APP_ENV == "production":
    if app.config["SECRET_KEY"] == "dev-secret-key-sanitario-crh":
        raise RuntimeError("FLASK_SECRET_KEY es obligatoria y debe ser segura en producción.")
    if ADMIN_PASSWORD == "admin123" and not ADMIN_PASSWORD_HASH:
        raise RuntimeError("Configurá ADMIN_PASSWORD_HASH o una ADMIN_PASSWORD segura en producción.")

# Serializer para generar/validar los tokens del enlace "Olvidé mi contraseña".
serializer_recuperacion = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="recuperar-password")

_intentos: dict[str, list[float]] = {}


def excede_limite(clave: str, max_intentos: int, ventana_segundos: int) -> bool:
    ahora = time.time()
    recientes = [t for t in _intentos.get(clave, []) if ahora - t < ventana_segundos]
    if len(recientes) >= max_intentos:
        _intentos[clave] = recientes
        return True
    recientes.append(ahora)
    _intentos[clave] = recientes
    return False


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def proteger_csrf():
    if request.method == "POST":
        enviado = request.form.get("csrf_token", "")
        esperado = session.get("csrf_token", "")
        if not enviado or not esperado or not hmac.compare_digest(enviado, esperado):
            return "Solicitud inválida o vencida. Actualizá la página e intentá nuevamente.", 400


@app.after_request
def encabezados_seguridad(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'self'"
    )
    if request.path.startswith("/admin") or request.path.startswith("/mi-portal"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Conexión a la base de datos (una por request, reutilizable con `g`)
# ---------------------------------------------------------------------------
def _configurar_conexion(conn: sqlite3.Connection) -> None:
    """Aplica pragmas que evitan errores de 'database is locked' cuando hay
    varios workers de gunicorn escribiendo a la vez (causa típica de fallas
    silenciosas al registrarse o iniciar sesión)."""
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        _configurar_conexion(g.db)
    return g.db


@app.teardown_appcontext
def cerrar_db(exception=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def login_requerido(vista):
    """Exige que haya un donante autenticado antes de acceder a la vista."""
    @functools.wraps(vista)
    def envoltorio(*args, **kwargs):
        if "user_id" not in session:
            flash("Iniciá sesión para continuar.", "warning")
            return redirect(url_for("login"))
        return vista(*args, **kwargs)
    return envoltorio


def enviar_email_recuperacion(destinatario: str, reset_url: str) -> bool:
    """Envía el correo con el enlace de restablecimiento de contraseña.

    Si no hay SMTP configurado (por ejemplo, en desarrollo local) la función
    devuelve False y el llamador puede optar por mostrar el enlace en pantalla
    o en los logs, para no bloquear el flujo de pruebas.
    """
    cuerpo = (
        "Recibimos una solicitud para restablecer tu contraseña en el "
        "Centro Regional de Hemoterapia de Jujuy.\n\n"
        f"Para crear una nueva contraseña, ingresá a este enlace (válido por 1 hora):\n{reset_url}\n\n"
        "Si vos no solicitaste este cambio, podés ignorar este mensaje."
    )
    mensaje = MIMEText(cuerpo)
    mensaje["Subject"] = "Recuperación de contraseña · CRH Jujuy"
    mensaje["From"] = SMTP_FROM
    mensaje["To"] = destinatario

    if not SMTP_HOST:
        app.logger.warning("SMTP no configurado. Enlace de recuperación para %s: %s", destinatario, reset_url)
        return False

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as servidor:
            servidor.starttls()
            if SMTP_USER and SMTP_PASSWORD:
                servidor.login(SMTP_USER, SMTP_PASSWORD)
            servidor.sendmail(SMTP_FROM, [destinatario], mensaje.as_string())
        return True
    except (smtplib.SMTPException, OSError) as error:
        app.logger.error("No se pudo enviar el email de recuperación a %s: %s", destinatario, error)
        return False


def init_db_if_missing() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        _configurar_conexion(conn)
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_donaciones_donante_fecha ON donaciones(donante_id, fecha_donacion)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_turnos_campana_hora ON turnos(campana_id, hora)")
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


def calcular_elegibilidad(donante: sqlite3.Row, ultima_donacion_str: Optional[str], historial=None) -> tuple[str, str]:
    """Determina el estado clínico del donante: Apto, En espera o No apto.

    Si hay una decisión médica manual cargada, esa decisión tiene prioridad
    sobre el cálculo automático.
    """
    if donante["estado_manual"] != "Automatico":
        return donante["estado_manual"], donante["observaciones_medicas"] or "Decisión médica manual."

    edad = calcular_edad(donante["fecha_nacimiento"])
    if edad is None:
        return "No apto", "Fecha de nacimiento inválida."
    if edad < EDAD_MINIMA:
        return "No apto", "Menor de 18 años: requiere autorización y evaluación profesional."
    if edad > EDAD_MAXIMA:
        return "No apto", "Mayor de 65 años: requiere certificado médico y evaluación profesional."

    peso = donante["peso_kg"] if "peso_kg" in donante.keys() else None
    if peso is None:
        return "No apto", "Falta registrar el peso para completar la preclasificación."
    if peso <= 50:
        return "No apto", "El requisito general es pesar más de 50 kg."
    if "embarazo" in donante.keys() and donante["embarazo"]:
        return "No apto", "El embarazo impide donar según los criterios generales de preselección."

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
        return "No apto", "La medicación declarada requiere evaluación profesional antes de donar."

    if historial:
        limite = datetime.now().date() - timedelta(days=365)
        donaciones_periodo = 0
        for item in historial:
            try:
                if datetime.strptime(item["fecha_donacion"], "%Y-%m-%d").date() >= limite:
                    donaciones_periodo += 1
            except (ValueError, TypeError):
                continue
        maximo = MAX_DONACIONES_ANUALES.get(donante["genero"], 3)
        if donaciones_periodo >= maximo:
            return "En espera", f"Alcanzó el máximo de {maximo} donaciones en los últimos 12 meses."

    if ultima_donacion_str:
        try:
            ultima_don = datetime.strptime(ultima_donacion_str, "%Y-%m-%d").date()
            intervalo = VENTANA_DIAS.get(donante["genero"], 60)
            fecha_regreso = ultima_don + timedelta(days=intervalo)
            if hoy < fecha_regreso:
                dias_restantes = (fecha_regreso - hoy).days
                return "En espera", f"Faltan {dias_restantes} días. Podrás volver a donar el {fecha_regreso.strftime('%d/%m/%Y')}."
        except ValueError:
            pass

    return "Apto", "Cumple la preselección automática; la aptitud final se confirma en la entrevista profesional."


def obtener_historial(donante_id: int) -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM donaciones WHERE donante_id = ? ORDER BY fecha_donacion DESC",
        (donante_id,),
    ).fetchall()


def validar_nueva_donacion(
    donante: sqlite3.Row,
    historial: list[sqlite3.Row],
    fecha_nueva_str: str,
) -> tuple[bool, str]:
    """Valida si corresponde permitir el registro de una nueva donación.

    Devuelve (True, "") si se puede registrar, o (False, motivo) si debe
    bloquearse. Se aplican, en orden:

      1) Estado "No Apto Manual" cargado por un profesional: bloquea siempre,
         sin excepciones, hasta que se revierta esa decisión.
      2) Elegibilidad clínica automática vigente (edad, tatuaje/piercing
         reciente, medicación): si hoy el donante figura "No apto", se
         bloquea el registro de la donación.
      3) Fecha de la donación: debe ser una fecha válida y no futura.
      4) Ventana sanitaria mínima entre donaciones (VENTANA_DIAS, según
         género): se compara la fecha de la nueva donación contra la fecha
         de la última donación registrada, para impedir cargar donaciones en
         días consecutivos o antes de cumplirse el plazo mínimo.
    """
    # 1) Decisión médica manual explícita: tiene prioridad y bloquea siempre.
    if donante["estado_manual"] == "No Apto Manual":
        return False, (
            "El donante está marcado como 'No Apto Manual'. No se pueden "
            "registrar donaciones hasta que se actualice esa decisión."
        )

    # 2) Elegibilidad clínica automática vigente (no depende de la fecha
    # cargada, sino del estado actual del donante).
    ultima_str_actual = historial[0]["fecha_donacion"] if historial else None
    estado_actual, motivo_actual = calcular_elegibilidad(donante, ultima_str_actual, historial)
    if estado_actual == "No apto":
        return False, f"El donante no está apto para donar: {motivo_actual}"

    # 3) Validamos que la fecha de la nueva donación sea correcta.
    try:
        fecha_nueva = datetime.strptime(fecha_nueva_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, "La fecha de la donación no es válida."

    if fecha_nueva > datetime.now().date():
        return False, "La fecha de la donación no puede ser futura."

    # 4) Ventana sanitaria mínima respecto de la última donación registrada,
    # para impedir registrar donaciones en días consecutivos o antes de
    # tiempo.
    if historial:
        try:
            fecha_anterior = datetime.strptime(historial[0]["fecha_donacion"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            fecha_anterior = None

        if fecha_anterior is not None:
            intervalo = VENTANA_DIAS.get(donante["genero"], 60)
            dias_transcurridos = (fecha_nueva - fecha_anterior).days
            if dias_transcurridos < intervalo:
                fecha_habilitada = fecha_anterior + timedelta(days=intervalo)
                return False, (
                    f"No se puede registrar: deben transcurrir al menos {intervalo} días "
                    f"entre donaciones. Última donación registrada: "
                    f"{fecha_anterior.strftime('%d/%m/%Y')}. Próxima fecha habilitada: "
                    f"{fecha_habilitada.strftime('%d/%m/%Y')}."
                )

    return True, ""


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
        "SELECT * FROM campanas WHERE publicada = 1 AND fecha >= date('now') ORDER BY fecha ASC LIMIT 6"
    ).fetchall()
    campanas = [dict(
        c,
        fecha_legible=fecha_legible(c["fecha"]),
        whatsapp_url=f"https://wa.me/{WHATSAPP_CONSULTAS}?text={quote('Hola, quisiera consultar sobre la campaña ' + c['titulo'] + ' del ' + fecha_legible(c['fecha']) + '.')}"
    ) for c in campanas]
    return render_template(
        "landing.html",
        total_donantes=total_donantes,
        total_donaciones=total_donaciones,
        campanas=campanas,
    )


@app.route("/informacion")
def informacion():
    mensaje = quote("Hola, quisiera realizar una consulta sobre la donación de sangre.")
    return render_template("informacion.html", whatsapp_consultas_url=f"https://wa.me/{WHATSAPP_CONSULTAS}?text={mensaje}")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    dni_duplicado = False
    if request.method == "POST":
        db = get_db()
        dni = request.form.get("dni", "").strip()
        email = request.form.get("email", "").strip().lower()
        f_tat = request.form.get("fecha_ultimo_tatuaje") or None
        f_pier = request.form.get("fecha_ultimo_piercing") or None
        toma_medicacion = 1 if request.form.get("toma_medicacion") else 0
        embarazo = 1 if request.form.get("embarazo") else 0
        peso = request.form.get("peso_kg", type=float)

        nombre = request.form.get("nombre", "").strip()
        password = request.form.get("password", "")
        fecha_nacimiento = request.form.get("fecha_nacimiento", "")
        genero = request.form.get("genero", "")
        grupo = request.form.get("grupo_sanguineo", "")
        factor = request.form.get("factor_rh", "")
        localidad = request.form.get("localidad", "")
        errores = []
        if not re.fullmatch(r"\d{7,9}", dni): errores.append("El DNI debe contener entre 7 y 9 números.")
        if not nombre or len(nombre) > 120: errores.append("Ingresá un nombre válido.")
        if calcular_edad(fecha_nacimiento) is None: errores.append("La fecha de nacimiento no es válida.")
        if genero not in GENDERS: errores.append("El género seleccionado no es válido.")
        if grupo not in BLOOD_GROUPS or factor not in RH_OPTIONS: errores.append("El grupo sanguíneo no es válido.")
        if localidad not in LOCALIDADES: errores.append("La localidad seleccionada no es válida.")
        if peso is None or not 30 <= peso <= 250: errores.append("Ingresá un peso válido entre 30 y 250 kg.")
        if embarazo and genero != "F": errores.append("Revisá el dato de embarazo y género seleccionado.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): errores.append("El correo electrónico no es válido.")
        if len(password) < 10: errores.append("La contraseña debe tener al menos 10 caracteres.")
        if errores:
            for error in errores: flash(error, "warning")
            return render_template("registro.html", localidades=LOCALIDADES, blood_groups=BLOOD_GROUPS,
                                   rh_options=RH_OPTIONS, dni_duplicado=False, valores=request.form)

        # Verificamos duplicados ANTES de intentar insertar para poder dar un
        # mensaje claro y ofrecer la recuperación de contraseña en el momento.
        existente_dni = db.execute("SELECT id FROM donantes WHERE dni = ?", (dni,)).fetchone()
        if existente_dni:
            dni_duplicado = True
            flash(
                "El donante ya se encuentra registrado. Si es tu ficha, podés "
                "recuperar tu contraseña en lugar de crear una nueva.",
                "warning",
            )
        else:
            existente_email = db.execute("SELECT id FROM donantes WHERE email = ?", (email,)).fetchone()
            if existente_email:
                flash("Ese correo electrónico ya está en uso por otra cuenta.", "warning")
            else:
                try:
                    db.execute(
                        """INSERT INTO donantes
                           (dni, nombre, fecha_nacimiento, genero, grupo_sanguineo, factor_rh,
                            localidad, email, telefono, password_hash,
                            fecha_ultimo_tatuaje, fecha_ultimo_piercing, toma_medicacion, peso_kg, embarazo)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            dni, nombre, fecha_nacimiento, genero, grupo, factor,
                            localidad, email, request.form.get("telefono", "").strip(),
                            generate_password_hash(password),
                            f_tat, f_pier, toma_medicacion, peso, embarazo,
                        ),
                    )
                    db.commit()

                    # Verificación defensiva: confirmamos que el registro haya
                    # quedado realmente persistido antes de avisar éxito al
                    # usuario, para no informar un registro exitoso que en
                    # realidad falló silenciosamente.
                    guardado = db.execute("SELECT id FROM donantes WHERE dni = ?", (dni,)).fetchone()
                    if guardado:
                        flash("Registro exitoso. Ya podés iniciar sesión.", "success")
                        return redirect(url_for("login"))
                    flash("No pudimos confirmar el registro. Por favor, intentá nuevamente.", "danger")
                except sqlite3.IntegrityError:
                    flash("Error: el DNI o el correo ya están registrados.", "warning")
                except sqlite3.OperationalError:
                    app.logger.exception("Error operacional de SQLite al registrar un donante.")
                    flash(
                        "El sistema está ocupado en este momento. Por favor, "
                        "intentá nuevamente en unos segundos.",
                        "danger",
                    )

    return render_template(
        "registro.html",
        localidades=LOCALIDADES,
        blood_groups=BLOOD_GROUPS,
        rh_options=RH_OPTIONS,
        dni_duplicado=dni_duplicado,
        valores=request.form if request.method == "POST" else {},
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if excede_limite(f"login:{request.remote_addr}:{email}", 8, 15 * 60):
            flash("Demasiados intentos. Esperá unos minutos antes de volver a intentar.", "danger")
            return render_template("login.html"), 429
        try:
            res = get_db().execute("SELECT * FROM donantes WHERE email = ?", (email,)).fetchone()
        except sqlite3.OperationalError:
            app.logger.exception("Error operacional de SQLite al iniciar sesión.")
            flash("El sistema está ocupado en este momento. Probá nuevamente en unos segundos.", "danger")
            return render_template("login.html")

        if res and check_password_hash(res["password_hash"], password):
            session.clear()
            session.permanent = True
            session["user_id"] = res["id"]
            session["user_name"] = res["nombre"]
            return redirect(url_for("perfil_donante"))
        flash("Credenciales incorrectas.", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Recuperación de contraseña
# ---------------------------------------------------------------------------
@app.route("/recuperar-password", methods=["GET", "POST"])
def recuperar_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if excede_limite(f"reset:{request.remote_addr}:{email}", 5, 60 * 60):
            flash("Si el correo está registrado, vas a recibir un enlace de recuperación.", "success")
            return redirect(url_for("login"))
        donante = get_db().execute("SELECT id, email FROM donantes WHERE email = ?", (email,)).fetchone()

        if donante:
            hash_actual = get_db().execute("SELECT password_hash FROM donantes WHERE id = ?", (donante["id"],)).fetchone()[0]
            token = serializer_recuperacion.dumps({"id": donante["id"], "v": hash_actual[-16:]})
            reset_url = url_for("resetear_password", token=token, _external=True)
            enviado = enviar_email_recuperacion(donante["email"], reset_url)
            if enviado:
                flash("Te enviamos un correo con el enlace para restablecer tu contraseña.", "success")
            elif app.debug and ALLOW_DEMO_RESET_LINK:
                # Sin SMTP configurado (entorno de desarrollo): mostramos el
                # enlace directamente para no bloquear las pruebas.
                flash(
                    "No se pudo enviar el email (SMTP no configurado en este entorno). "
                    f"Podés usar este enlace de prueba: {reset_url}",
                    "warning",
                )
            else:
                flash("Si el correo está registrado, vas a recibir un enlace de recuperación.", "success")
        else:
            # Mensaje genérico: no confirmamos ni negamos si el email existe,
            # para no filtrar qué correos están registrados en el sistema.
            flash("Si el correo está registrado, vas a recibir un enlace de recuperación.", "success")
        return redirect(url_for("login"))

    return render_template("recuperar_password.html")


@app.route("/resetear-password/<token>", methods=["GET", "POST"])
def resetear_password(token):
    try:
        datos_token = serializer_recuperacion.loads(token, max_age=RESET_TOKEN_MAX_AGE)
        donante_id = datos_token["id"]
    except SignatureExpired:
        flash("El enlace de recuperación venció. Solicitá uno nuevo.", "warning")
        return redirect(url_for("recuperar_password"))
    except (BadSignature, TypeError, KeyError):
        flash("El enlace de recuperación no es válido.", "danger")
        return redirect(url_for("recuperar_password"))

    db = get_db()
    donante = db.execute("SELECT id, nombre, password_hash FROM donantes WHERE id = ?", (donante_id,)).fetchone()
    if donante is None:
        flash("No encontramos la cuenta asociada a este enlace.", "danger")
        return redirect(url_for("recuperar_password"))
    if datos_token.get("v") != donante["password_hash"][-16:]:
        flash("Este enlace ya fue utilizado o dejó de ser válido.", "warning")
        return redirect(url_for("recuperar_password"))

    if request.method == "POST":
        nueva_password = request.form.get("password", "")
        confirmar_password = request.form.get("confirmar_password", "")
        if len(nueva_password) < 10:
            flash("La contraseña debe tener al menos 10 caracteres.", "warning")
        elif nueva_password != confirmar_password:
            flash("Las contraseñas no coinciden.", "warning")
        else:
            db.execute(
                "UPDATE donantes SET password_hash = ? WHERE id = ?",
                (generate_password_hash(nueva_password), donante_id),
            )
            db.commit()
            flash("Tu contraseña se actualizó correctamente. Ya podés iniciar sesión.", "success")
            return redirect(url_for("login"))

    return render_template("resetear_password.html", nombre=donante["nombre"])


# ---------------------------------------------------------------------------
# Portal del donante
# ---------------------------------------------------------------------------
@app.route("/mi-portal")
@login_requerido
def perfil_donante():
    db = get_db()
    donante = db.execute("SELECT * FROM donantes WHERE id = ?", (session["user_id"],)).fetchone()
    if donante is None:
        session.clear()
        return redirect(url_for("login"))

    historial = obtener_historial(donante["id"])
    ultima_donacion_str = historial[0]["fecha_donacion"] if historial else None
    estado, motivo = calcular_elegibilidad(donante, ultima_donacion_str, historial)

    campanas = db.execute(
        "SELECT * FROM campanas WHERE publicada = 1 AND localidad = ? AND fecha >= date('now') ORDER BY fecha ASC",
        (donante["localidad"],),
    ).fetchall()
    campanas = [dict(
        c,
        whatsapp_url=f"https://wa.me/{WHATSAPP_CONSULTAS}?text={quote('Hola, quisiera consultar sobre la campaña ' + c['titulo'] + ' del ' + fecha_legible(c['fecha']) + '.')}"
    ) for c in campanas]
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
        estados_ayuda=ESTADOS_AYUDA,
    )


@app.route("/constancia/<int:donacion_id>.pdf")
def constancia_pdf(donacion_id):
    db = get_db()
    donacion = db.execute(
        """SELECT dn.*, d.nombre, d.dni, d.grupo_sanguineo, d.factor_rh
           FROM donaciones dn JOIN donantes d ON d.id = dn.donante_id
           WHERE dn.id = ?""",
        (donacion_id,),
    ).fetchone()
    if donacion is None:
        abort(404)
    es_admin = admin_requerido()
    es_titular = session.get("user_id") == donacion["donante_id"]
    if not es_admin and not es_titular:
        abort(403)

    salida = BytesIO()
    doc = SimpleDocTemplate(salida, pagesize=A4, rightMargin=24*mm, leftMargin=24*mm,
                            topMargin=24*mm, bottomMargin=24*mm,
                            title=f"Constancia de donación - {donacion['nombre']}")
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("TituloCRH", parent=estilos["Title"], alignment=TA_CENTER,
                            textColor=colors.HexColor("#7A1220"), fontSize=20, leading=24)
    centro = ParagraphStyle("Centro", parent=estilos["BodyText"], alignment=TA_CENTER, leading=16)
    historia = [
        Paragraph("Centro Regional de Hemoterapia de Jujuy", titulo),
        Spacer(1, 8*mm),
        Paragraph("CONSTANCIA DE DONACIÓN DE SANGRE", centro),
        Spacer(1, 12*mm),
        Paragraph(
            "Se deja constancia de que la persona detallada a continuación registra una donación en el sistema institucional.",
            estilos["BodyText"],
        ),
        Spacer(1, 7*mm),
    ]
    datos = [
        ["Donante", donacion["nombre"]],
        ["DNI", donacion["dni"]],
        ["Grupo sanguíneo", f"{donacion['grupo_sanguineo']}{donacion['factor_rh']}"],
        ["Fecha de donación", fecha_legible(donacion["fecha_donacion"])],
        ["Volumen registrado", f"{donacion['volumen_ml']} ml" if donacion["volumen_ml"] else "No informado"],
        ["Código de constancia", f"CRH-{donacion['id']:08d}"],
    ]
    tabla = Table(datos, colWidths=[48*mm, 105*mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3DEDD")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#7A1220")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7C7C3")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 9),
    ]))
    historia.extend([
        tabla, Spacer(1, 18*mm),
        Paragraph("Documento generado por el sistema de gestión de donantes. Su validez institucional debe ser confirmada por el Centro Regional de Hemoterapia.", centro),
    ])
    doc.build(historia)
    salida.seek(0)
    return send_file(salida, mimetype="application/pdf", as_attachment=True,
                     download_name=f"constancia_donacion_{donacion['id']}.pdf")


@app.route("/mi-portal/editar", methods=["GET", "POST"])
@login_requerido
def editar_perfil():
    db = get_db()
    donante = db.execute("SELECT * FROM donantes WHERE id = ?", (session["user_id"],)).fetchone()
    if donante is None:
        session.clear()
        return redirect(url_for("login"))

    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        email = request.form.get("email", "").strip().lower()
        telefono = request.form.get("telefono", "").strip()
        localidad = request.form.get("localidad", "").strip()
        f_tat = request.form.get("fecha_ultimo_tatuaje") or None
        f_pier = request.form.get("fecha_ultimo_piercing") or None
        toma_medicacion = 1 if request.form.get("toma_medicacion") else 0
        embarazo = 1 if request.form.get("embarazo") else 0
        peso = request.form.get("peso_kg", type=float)

        password_actual = request.form.get("password_actual", "")
        password_nueva = request.form.get("password_nueva", "")
        password_confirmar = request.form.get("password_confirmar", "")

        errores = []
        if not nombre or not email or not localidad:
            errores.append("Nombre, correo y localidad son obligatorios.")
        if peso is None or not 30 <= peso <= 250:
            errores.append("Ingresá un peso válido entre 30 y 250 kg.")

        cambia_password = bool(password_nueva or password_confirmar or password_actual)
        if cambia_password:
            if not check_password_hash(donante["password_hash"], password_actual):
                errores.append("La contraseña actual no es correcta.")
            elif len(password_nueva) < 10:
                errores.append("La nueva contraseña debe tener al menos 10 caracteres.")
            elif password_nueva != password_confirmar:
                errores.append("Las contraseñas nuevas no coinciden.")

        if errores:
            for error in errores:
                flash(error, "warning")
        else:
            try:
                if cambia_password:
                    db.execute(
                        """UPDATE donantes
                           SET nombre = ?, email = ?, telefono = ?, localidad = ?,
                               fecha_ultimo_tatuaje = ?, fecha_ultimo_piercing = ?,
                               toma_medicacion = ?, peso_kg = ?, embarazo = ?, password_hash = ?
                           WHERE id = ?""",
                        (nombre, email, telefono, localidad, f_tat, f_pier, toma_medicacion, peso, embarazo,
                         generate_password_hash(password_nueva), donante["id"]),
                    )
                else:
                    db.execute(
                        """UPDATE donantes
                           SET nombre = ?, email = ?, telefono = ?, localidad = ?,
                               fecha_ultimo_tatuaje = ?, fecha_ultimo_piercing = ?,
                               toma_medicacion = ?, peso_kg = ?, embarazo = ?
                           WHERE id = ?""",
                        (nombre, email, telefono, localidad, f_tat, f_pier, toma_medicacion, peso, embarazo,
                         donante["id"]),
                    )
                db.commit()
                session["user_name"] = nombre
                flash("Tus datos se actualizaron correctamente.", "success")
                return redirect(url_for("perfil_donante"))
            except sqlite3.IntegrityError:
                flash("Ese correo electrónico ya está en uso por otra cuenta.", "warning")
            except sqlite3.OperationalError:
                app.logger.exception("Error operacional de SQLite al editar el perfil.")
                flash("El sistema está ocupado en este momento. Probá nuevamente en unos segundos.", "danger")

        # Si hubo errores, volvemos a renderizar con los valores que el
        # usuario tipeó (excepto contraseñas) para no perder lo ya cargado.
        donante = dict(donante)
        donante.update({
            "nombre": nombre, "email": email, "telefono": telefono, "localidad": localidad,
            "fecha_ultimo_tatuaje": f_tat, "fecha_ultimo_piercing": f_pier,
            "toma_medicacion": toma_medicacion,
            "peso_kg": peso, "embarazo": embarazo,
        })

    return render_template(
        "editar_perfil.html", donante=donante, localidades=LOCALIDADES,
    )


@app.route("/reservar-turno", methods=["POST"])
def reservar_turno():
    if "user_id" not in session:
        return redirect(url_for("login"))
    db = get_db()
    donante = db.execute("SELECT * FROM donantes WHERE id = ?", (session["user_id"],)).fetchone()
    campana_id = request.form.get("campana_id", type=int)
    hora = request.form.get("hora", "").strip()
    campana = db.execute("SELECT * FROM campanas WHERE id = ?", (campana_id,)).fetchone()
    historial = obtener_historial(donante["id"]) if donante else []
    ultima = historial[0]["fecha_donacion"] if historial else None
    estado, _ = calcular_elegibilidad(donante, ultima, historial) if donante else ("No apto", "")
    if (not donante or not campana or not campana["publicada"] or campana["fecha"] < datetime.now().date().isoformat()
            or campana["localidad"] != donante["localidad"] or estado not in ("Apto", "Apto Manual")
            or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hora)):
        flash("No fue posible reservar ese turno. Verificá campaña, fecha, localidad, horario y aptitud.", "danger")
        return redirect(url_for("perfil_donante"))
    ocupado = db.execute("SELECT 1 FROM turnos WHERE campana_id = ? AND hora = ?", (campana_id, hora)).fetchone()
    if ocupado:
        flash("Ese horario ya fue reservado. Elegí otro.", "warning")
        return redirect(url_for("perfil_donante"))
    try:
        db.execute(
            "INSERT INTO turnos (donante_id, campana_id, hora) VALUES (?, ?, ?)",
            (session["user_id"], campana_id, hora),
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
        if excede_limite(f"admin:{request.remote_addr}:{usuario}", 6, 15 * 60):
            flash("Demasiados intentos. Esperá unos minutos.", "danger")
            return render_template("admin_login.html"), 429
        usuario_ok = hmac.compare_digest(usuario, ADMIN_USER)
        clave_ok = check_password_hash(ADMIN_PASSWORD_HASH, clave) if ADMIN_PASSWORD_HASH else hmac.compare_digest(clave, ADMIN_PASSWORD)
        if usuario_ok and clave_ok:
            session.clear()
            session.permanent = True
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
        if edad_min and edad_min.isdigit() and edad < int(edad_min):
            continue
        if edad_max and edad_max.isdigit() and edad > int(edad_max):
            continue
        historial = obtener_historial(fila["id"])
        ultima = historial[0]["fecha_donacion"] if historial else None
        estado, _ = calcular_elegibilidad(fila, ultima, historial)
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
        estados_ayuda=ESTADOS_AYUDA,
    )


def validar_campana(formulario):
    datos = {
        "titulo": formulario.get("titulo", "").strip(),
        "fecha": formulario.get("fecha", "").strip(),
        "horario": formulario.get("horario", "").strip(),
        "ubicacion": formulario.get("ubicacion", "").strip(),
        "localidad": formulario.get("localidad", "").strip(),
        "publicada": 1 if formulario.get("publicada") else 0,
    }
    errores = []
    if not datos["titulo"] or len(datos["titulo"]) > 150: errores.append("Ingresá un título válido.")
    try:
        fecha = datetime.strptime(datos["fecha"], "%Y-%m-%d").date()
        if fecha < datetime.now().date(): errores.append("La campaña no puede publicarse con fecha pasada.")
    except ValueError:
        errores.append("La fecha no es válida.")
    if not re.fullmatch(r"\d{2}:\d{2}\s+a\s+\d{2}:\d{2}", datos["horario"]):
        errores.append("Usá el formato de horario 08:00 a 12:00.")
    else:
        inicio, fin = datos["horario"].split(" a ")
        try:
            hora_inicio = datetime.strptime(inicio, "%H:%M")
            hora_fin = datetime.strptime(fin, "%H:%M")
            if hora_fin <= hora_inicio: errores.append("El horario de finalización debe ser posterior al inicio.")
        except ValueError:
            errores.append("El horario no es válido.")
    if not datos["ubicacion"] or len(datos["ubicacion"]) > 180: errores.append("Ingresá una dirección válida.")
    if datos["localidad"] not in LOCALIDADES: errores.append("La localidad no es válida.")
    return datos, errores


@app.route("/admin/campanas/nueva", methods=["GET", "POST"])
def admin_campana_nueva():
    if not admin_requerido(): return redirect(url_for("admin_login"))
    valores = request.form if request.method == "POST" else {}
    if request.method == "POST":
        datos, errores = validar_campana(request.form)
        if not errores:
            db = get_db()
            db.execute("""INSERT INTO campanas (titulo, tipo, fecha, horario, ubicacion, localidad, publicada)
                          VALUES (?, 'Colecta de Sangre', ?, ?, ?, ?, ?)""",
                       (datos["titulo"], datos["fecha"], datos["horario"], datos["ubicacion"], datos["localidad"], datos["publicada"]))
            db.commit()
            flash("Campaña creada correctamente.", "success")
            return redirect(url_for("admin_dashboard"))
        for error in errores: flash(error, "warning")
    return render_template("admin_campana_form.html", campana=valores, localidades=LOCALIDADES, modo="Crear")


@app.route("/admin/campanas/<int:campana_id>/editar", methods=["GET", "POST"])
def admin_campana_editar(campana_id):
    if not admin_requerido(): return redirect(url_for("admin_login"))
    db = get_db()
    campana = db.execute("SELECT * FROM campanas WHERE id = ?", (campana_id,)).fetchone()
    if campana is None: abort(404)
    if request.method == "POST":
        datos, errores = validar_campana(request.form)
        if not errores:
            db.execute("""UPDATE campanas SET titulo=?, fecha=?, horario=?, ubicacion=?, localidad=?, publicada=?
                          WHERE id=?""", (datos["titulo"], datos["fecha"], datos["horario"], datos["ubicacion"], datos["localidad"], datos["publicada"], campana_id))
            db.commit()
            flash("Campaña actualizada.", "success")
            return redirect(url_for("admin_dashboard"))
        for error in errores: flash(error, "warning")
        campana = request.form
    return render_template("admin_campana_form.html", campana=campana, localidades=LOCALIDADES, modo="Editar")


@app.route("/admin/campanas/<int:campana_id>/eliminar", methods=["POST"])
def admin_campana_eliminar(campana_id):
    if not admin_requerido(): return redirect(url_for("admin_login"))
    db = get_db()
    turnos = db.execute("SELECT COUNT(*) FROM turnos WHERE campana_id = ?", (campana_id,)).fetchone()[0]
    if turnos:
        db.execute("UPDATE campanas SET publicada = 0 WHERE id = ?", (campana_id,))
        flash("La campaña tiene turnos: se despublicó para preservar el historial.", "warning")
    else:
        db.execute("DELETE FROM campanas WHERE id = ?", (campana_id,))
        flash("Campaña eliminada.", "success")
    db.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/estadisticas")
def admin_estadisticas():
    if not admin_requerido(): return redirect(url_for("admin_login"))
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM donantes").fetchone()[0]
    localidades = db.execute("SELECT localidad etiqueta, COUNT(*) cantidad FROM donantes GROUP BY localidad ORDER BY cantidad DESC").fetchall()
    grupos = db.execute("""SELECT grupo_sanguineo || factor_rh etiqueta, COUNT(*) cantidad
                           FROM donantes GROUP BY grupo_sanguineo, factor_rh ORDER BY cantidad DESC""").fetchall()
    max_localidad = max([x["cantidad"] for x in localidades] or [1])
    max_grupo = max([x["cantidad"] for x in grupos] or [1])
    return render_template("admin_estadisticas.html", total=total, localidades=localidades, grupos=grupos,
                           max_localidad=max_localidad, max_grupo=max_grupo)


@app.route("/admin/notificaciones")
def admin_notificaciones():
    if not admin_requerido(): return redirect(url_for("admin_login"))
    q = request.args.get("q", "").strip()
    grupo = request.args.get("grupo", "").strip()
    factor = request.args.get("factor", "").strip()
    localidad = request.args.get("localidad", "").strip()
    zona = request.args.get("zona", "").strip()
    incluir_no_aptos = request.args.get("incluir_no_aptos") == "1"
    campana_id = request.args.get("campana_id", type=int)
    mensaje = request.args.get("mensaje", "").strip()
    db = get_db()
    condiciones, parametros = ["telefono IS NOT NULL", "TRIM(telefono) <> ''"], []
    if q: condiciones.append("(nombre LIKE ? OR dni LIKE ?)"); parametros.extend([f"%{q}%", f"%{q}%"])
    if grupo in BLOOD_GROUPS: condiciones.append("grupo_sanguineo = ?"); parametros.append(grupo)
    if factor in RH_OPTIONS: condiciones.append("factor_rh = ?"); parametros.append(factor)
    if localidad in LOCALIDADES: condiciones.append("localidad = ?"); parametros.append(localidad)
    elif zona in ZONAS:
        lugares = ZONAS[zona]
        condiciones.append(f"localidad IN ({','.join('?' for _ in lugares)})")
        parametros.extend(lugares)
    donantes = db.execute(f"SELECT * FROM donantes WHERE {' AND '.join(condiciones)} ORDER BY nombre", parametros).fetchall()
    campanas = db.execute("SELECT * FROM campanas WHERE publicada=1 AND fecha >= date('now') ORDER BY fecha").fetchall()
    campana = db.execute("SELECT * FROM campanas WHERE id=?", (campana_id,)).fetchone() if campana_id else None
    if not mensaje and campana:
        mensaje = f"Centro Regional de Hemoterapia de Jujuy: te invitamos a {campana['titulo']} el {fecha_legible(campana['fecha'])}, {campana['horario']}, en {campana['ubicacion']} ({campana['localidad']})."
    filas = []
    descartados = 0
    for d in donantes:
        historial = obtener_historial(d["id"])
        ultima = historial[0]["fecha_donacion"] if historial else None
        estado, motivo = calcular_elegibilidad(d, ultima, historial)
        if estado not in ("Apto", "Apto Manual") and not incluir_no_aptos:
            descartados += 1
            continue
        telefono = re.sub(r"\D", "", d["telefono"] or "")
        if telefono and not telefono.startswith("54"): telefono = "549" + telefono.lstrip("0")
        elif telefono.startswith("54") and not telefono.startswith("549"): telefono = "549" + telefono[2:].lstrip("0")
        filas.append({**dict(d), "estado_calculado": estado, "motivo_estado": motivo,
                      "whatsapp_url": f"https://wa.me/{telefono}?text={quote(mensaje)}" if telefono and mensaje else ""})
    return render_template("admin_notificaciones.html", donantes=filas, campanas=campanas, mensaje=mensaje,
                           blood_groups=BLOOD_GROUPS, rh_options=RH_OPTIONS, localidades=LOCALIDADES,
                           zonas=ZONAS, q=q, grupo=grupo, factor=factor, localidad=localidad, zona=zona,
                           incluir_no_aptos=incluir_no_aptos, descartados=descartados, campana_id=campana_id)


@app.route("/admin/paciente/<int:id>", methods=["GET", "POST"])
def admin_detalle_paciente(id):
    if not admin_requerido():
        return redirect(url_for("admin_login"))

    db = get_db()

    # Necesitamos al donante ANTES de procesar el POST, porque tanto el
    # control de estado como la ventana entre donaciones dependen de sus
    # datos actuales (estado manual, género, historial, etc.).
    d = db.execute("SELECT * FROM donantes WHERE id = ?", (id,)).fetchone()
    if d is None:
        flash("Donante no encontrado.", "warning")
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_status":
            estado_solicitado = request.form.get("estado_manual")
            if estado_solicitado not in ("Automatico", "Apto Manual", "No Apto Manual"):
                flash("El estado solicitado no es válido.", "danger")
                return redirect(url_for("admin_detalle_paciente", id=id))
            db.execute(
                "UPDATE donantes SET estado_manual = ?, observaciones_medicas = ? WHERE id = ?",
                (estado_solicitado, request.form.get("observaciones_medicas", "").strip()[:2000], id),
            )
            db.commit()
            flash("Estado actualizado.", "success")
        elif action == "add_donation":
            fecha_nueva = request.form.get("fecha_donacion", "").strip()
            historial_actual = obtener_historial(id)

            # --- Validaciones críticas antes de registrar la donación ---
            valido, motivo_bloqueo = validar_nueva_donacion(d, historial_actual, fecha_nueva)
            if not valido:
                flash(motivo_bloqueo, "danger")
            else:
                volumen = request.form.get("volumen_ml", type=int)
                if volumen is not None and not 100 <= volumen <= 600:
                    flash("El volumen debe estar entre 100 y 600 ml.", "danger")
                    return redirect(url_for("admin_detalle_paciente", id=id))
                db.execute(
                    "INSERT INTO donaciones (donante_id, fecha_donacion, volumen_ml, observaciones) VALUES (?, ?, ?, ?)",
                    (id, fecha_nueva, volumen, request.form.get("observaciones", "").strip()[:2000]),
                )
                db.execute("UPDATE donantes SET estado_manual = 'Automatico' WHERE id = ?", (id,))
                db.commit()
                flash("Donación registrada y estado recalculado.", "success")

        elif action == "edit_donation_observation":
            # Permite corregir/completar la observación de una donación ya
            # registrada (p. ej. sede, colecta externa, reacciones, etc.).
            donacion_id = request.form.get("donacion_id", "").strip()
            nueva_observacion = request.form.get("observaciones_donacion", "").strip()

            # Verificamos que la donación exista y pertenezca a este donante
            # antes de modificarla, para evitar que se edite un registro de
            # otra persona manipulando el formulario.
            donacion = db.execute(
                "SELECT id FROM donaciones WHERE id = ? AND donante_id = ?",
                (donacion_id, id),
            ).fetchone()
            if donacion:
                db.execute(
                    "UPDATE donaciones SET observaciones = ? WHERE id = ?",
                    (nueva_observacion, donacion_id),
                )
                db.commit()
                flash("Observación de la donación actualizada.", "success")
            else:
                flash("No se encontró la donación indicada para editar.", "warning")

        # Volvemos a leer al donante por si su estado cambió durante el POST
        # (p. ej. tras update_status o tras registrar una donación).
        d = db.execute("SELECT * FROM donantes WHERE id = ?", (id,)).fetchone()

    historial = obtener_historial(id)
    ultima_donacion_str = historial[0]["fecha_donacion"] if historial else None
    estado, motivo = calcular_elegibilidad(d, ultima_donacion_str, historial)

    return render_template(
        "admin_detalle.html", d=d, historial=historial, estado=estado, motivo=motivo,
        hoy_iso=datetime.now().date().isoformat(), estados_ayuda=ESTADOS_AYUDA,
    )


init_db_if_missing()

if __name__ == "__main__":
    app.run(debug=APP_ENV == "development")
