import os
import sys
import pandas as pd
import smtplib
import importlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from werkzeug.utils import secure_filename

# Verificar dependencias críticas (ejemplo: openpyxl)
required_packages = ["openpyxl"]
missing_packages = []
for package in required_packages:
    try:
        importlib.import_module(package)
    except ImportError:
        missing_packages.append(package)

if missing_packages:
    print("ERROR: Faltan dependencias requeridas. Instale los siguientes paquetes:")
    for package in missing_packages:
        print(f"  pip install {package}")
    print("\nEl programa no puede continuar sin estas dependencias.")

# ================================
# Configuración de la aplicación
# ================================
class Settings:
    SECRET_KEY: str = "tu_clave_secreta_aqui"
    UPLOAD_FOLDER: str = "uploads"
    TEMPLATES_DIR: str = os.path.join(os.path.dirname(__file__), "templates")
    STATIC_DIR: str = os.path.join(os.path.dirname(__file__), "static")

settings = Settings()

# Asegurar directorios necesarios
os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(settings.STATIC_DIR, exist_ok=True)

# ================================
# Sistema de mensajes flash
# ================================
class Flash:
    def __init__(self):
        self.messages = []

    def get_messages(self):
        messages = self.messages.copy()
        self.messages.clear()
        return messages

    def add_message(self, message, category="info"):
        self.messages.append((category, message))

flash = Flash()

def get_flashed_messages(with_categories=False):
    messages = flash.get_messages()
    if with_categories:
        return messages
    return [message for _, message in messages]

# ================================
# Configuración SMTP
# ================================
SMTP_CONFIG = {
    "1": ("cflores.practica@cmpc.com", "ywsb sfgz fmyf qdsg", "personal"),
    "2": ("datadriven@cmpc.cl", "ccgu zixq lzme xmsr", "DataDriven"),
}

# ================================
# Función Factory para crear la app
# ================================
def create_app() -> FastAPI:
    """
    Factory function para crear y configurar la aplicación FastAPI.
    """
    app = FastAPI()

    # Middleware de sesión
    app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

    # Configurar templates
    templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)
    templates.env.globals["get_flashed_messages"] = get_flashed_messages

    # Montar archivos estáticos (CSS, JS, etc.)
    app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

    # ================================
    # Funciones de utilidad
    # ================================
    def clean_title(title):
        """
        Limpia y formatea el título del reporte eliminando texto adicional 
        y la extensión .docx.
        """
        if not pd.notna(title):
            return ""
        title = str(title)
        workspace_start = title.find(" - [")
        if (workspace_start != -1):
            title = title[:workspace_start]
        return title.strip().replace(".docx", "")

    def formatear_sellos(row):
        """
        Convierte los sellos booleanos a texto legible (Tecnología, Negocio, Seguridad).
        """
        sellos = []
        for sello, nombre in [
            ("SelloTécnico", "Tecnología"),
            ("SelloNegocio", "Negocio"),
            ("SelloSeguridad", "Seguridad"),
        ]:
            if row.get(sello, False):
                sellos.append(nombre)
        return " ; ".join(sellos) if sellos else "Sin sellos"

    def format_empty_value(value):
        """Reemplaza valores vacíos/NaN por 'Sin información'."""
        if pd.isna(value) or value == "" or str(value).lower() == "nan":
            return "Sin información"
        return str(value)

    def crear_contenido_html(reportes_por_dominio, owner_email):
        """
        Genera el contenido HTML para cada Owner, agrupando por dominio.
        Retorna el HTML final (o None si no hay reportes).
        """
        try:
            contenido_dominios = []
            # Ordena la lista de dominios para mostrarlos consistentemente
            dominios_ordenados = sorted(
                str(key)
                for key in reportes_por_dominio.keys()
                if pd.notna(key) and key is not None
            )

            for dominio in dominios_ordenados:
                if not dominio or not reportes_por_dominio[dominio]:
                    continue

                reportes = reportes_por_dominio[dominio]
                rows = []

                for reporte in reportes:
                    # Si viene como Series, convertirlo a dict
                    if isinstance(reporte, pd.Series):
                        reporte = reporte.to_dict()

                    rows.append(
                        f"""
                        <tr>
                            <td style="width: 25%; padding: 8px;">{format_empty_value(reporte.get('Workspace.Title', ''))}</td>
                            <td style="width: 35%; padding: 8px;">{format_empty_value(clean_title(reporte.get('Titulo', '')))}</td>
                            <td style="width: 20%; padding: 8px;">{format_empty_value(reporte.get('Responsable', ''))}</td>
                            <td style="width: 20%; padding: 8px;">{formatear_sellos(reporte)}</td>
                        </tr>
                        """
                    )

                if rows:
                    contenido_dominios.append(
                        f"""
                        <div class="dominio-section">
                            <h3 class="dominio-title">Dominio: {dominio}</h3>
                            <table class="reporte-table">
                                <tr>
                                    <th>Área PBI</th>
                                    <th>Título</th>
                                    <th>Responsable</th>
                                    <th>Sellos Actuales</th>
                                </tr>
                                {''.join(rows)}
                            </table>
                        </div>
                        """
                    )

            if not contenido_dominios:
                return None

            # Cargar la plantilla base del correo
            template_path = os.path.join(os.path.dirname(__file__), "email_template.html")
            with open(template_path, "r", encoding="utf-8") as file:
                template = file.read()

            # Retorna el contenido HTML final
            return (
                template.replace("{contenido_reportes}", "".join(contenido_dominios))
                .replace("{owner_email}", str(owner_email))
            )

        except Exception as e:
            print(f"Error en crear_contenido_html: {str(e)}")
            return None

    async def process_excel_file(filepath, email_option=None):
        """
        Procesa el archivo Excel, filtra los reportes,
        construye y envía los correos a cada Data Owner.
        """
        try:
            if not filepath or not os.path.exists(filepath):
                return "Error: Archivo no encontrado", []

            try:
                df = pd.read_excel(filepath, engine="openpyxl")
            except ImportError:
                return (
                    "Error: Falta la biblioteca openpyxl. Instale con 'pip install openpyxl'",
                    [],
                )

            # Normalizar valores de "Correo Enviado"
            df["Correo Enviado"] = df["Correo Enviado"].astype(str).str.lower().str.strip()

            # Convertir fechas correctamente
            df["Fecha envío"] = pd.to_datetime(df["Fecha envío"], errors='coerce')
            df["Fecha Compromiso"] = df["Fecha Compromiso"].astype(str).str.lower().str.strip()

            # Definir el umbral de 1 mes
            fecha_actual = pd.Timestamp.now()
            un_mes_atras = fecha_actual - pd.DateOffset(months=1)

            # Lista para almacenar las filas que sí cumplen con las condiciones
            filas_a_enviar = []

            # Recorrer fila por fila y aplicar las condiciones
            for _, row in df.iterrows():
                enviar_correo = False  # Flag para determinar si la fila debe incluirse

                # Condición 1: Si "Correo Enviado" es "No" en cualquier forma
                if row["Correo Enviado"] in ["no", "n", "no.", "n."]:
                    enviar_correo = True

                # Condición 2: Si el correo ya fue enviado, pero pasó más de un mes y "Fecha Compromiso" está vacía o pendiente
                elif row["Correo Enviado"] in ["correo enviado", "enviado", "sí", "si"]:
                    # 1) Verificar que existe una fecha de envío y que fue hace >= 1 mes
                    if pd.notna(row["Fecha envío"]) and row["Fecha envío"] <= un_mes_atras:
                        # 2) Verificar "Fecha Compromiso":
                        #    a) Puede venir como NaN de Excel (fila vacía), o
                        #    b) Puede ser una cadena vacía "", o
                        #    c) Puede ser "pendiente"
                        #    Si es "Ok", no reenviamos
                        fecha_compromiso_val = row["Fecha Compromiso"]
                        
                        # Primera check para detectar NaN (que no es cadena, sino float 'nan')
                        if pd.isna(fecha_compromiso_val):
                            enviar_correo = True
                        else:
                            # En caso de que no sea NaN, ya lo convertimos arriba a string
                            if fecha_compromiso_val in ["", "pendiente"]:
                                enviar_correo = True
                            # Si es "ok", no hacemos nada

                # Si la fila cumple alguna condición, se agrega a la lista
                if enviar_correo:
                    filas_a_enviar.append(row)

            # Crear un DataFrame solo con las filas que deben enviarse
            df_filtrado = pd.DataFrame(filas_a_enviar)

            if df_filtrado.empty:
                return "No hay correos que enviar", []

            # Credenciales SMTP
            sender_email, app_password, _ = SMTP_CONFIG[email_option]
            history_items = []

            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(sender_email, app_password)

                # Agrupar por Data Owner
                grouped = df_filtrado.groupby("DataOwner_Lgobierno")
                for owner, owner_df in grouped:
                    if not pd.notna(owner) or "@" not in str(owner):
                        continue

                    # Agrupar reportes por dominio
                    reportes_por_dominio = {}
                    for _, row in owner_df.iterrows():
                        dominio = str(row["Dominio"])
                        if dominio not in reportes_por_dominio:
                            reportes_por_dominio[dominio] = []
                        reportes_por_dominio[dominio].append(row)

                    # Crear contenido HTML
                    html_content = crear_contenido_html(reportes_por_dominio, owner)
                    if not html_content:
                        continue

                    msg = MIMEMultipart("alternative")
                    msg["From"] = sender_email
                    msg["To"] = owner
                    msg["Subject"] = "Reportes pendientes de asignación de sello de Negocio"
                    msg.attach(MIMEText(html_content, "html"))

                    # Manejo de CC
                    cc_list = []
                    stewards = owner_df["DataStewards"].dropna().unique()
                    for steward in stewards:
                        # Cada steward puede tener varias direcciones separadas por coma
                        cc_list.extend(
                            [
                                email.strip()
                                for email in str(steward).split(",")
                                if "@" in email.strip()
                            ]
                        )

                    # Agregamos siempre a Carolina a la lista de copias
                    cc_list.append("")

                    # Usamos un set para evitar duplicados
                    cc_list = list(set(cc_list))

                    if cc_list:
                        msg["Cc"] = ", ".join(cc_list)

                    # Recipientes finales
                    recipients = [owner] + cc_list

                    server.send_message(msg, to_addrs=recipients)

                    # Actualizar estado en el DataFrame original
                    df.loc[df["DataOwner_Lgobierno"] == owner, "Correo Enviado"] = "Correo enviado"
                    df.loc[df["DataOwner_Lgobierno"] == owner, "Fecha Envío"] = fecha_actual.strftime("%Y-%m-%d")

                    # Registrar en historial
                    history_items.append(
                        {
                            "owner": owner,
                            "domain": sorted(reportes_por_dominio.keys()),
                            "timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
                        }
                    )

            # Guardar cambios en el Excel
            df.to_excel(filepath, index=False)
            return "Proceso completado exitosamente", history_items

        except Exception as e:
            return f"Error: {str(e)}", []

    async def cleanup_session(request: Request):
        """Limpia el archivo temporal y las claves de sesión."""
        if "current_file" in request.session:
            try:
                os.remove(request.session["current_file"])
            except:
                pass
            request.session.pop("current_file", None)

        for key in ["summary", "email_summary", "email_option", "current_step"]:
            request.session.pop(key, None)

    # ================================
    # Rutas
    # ================================

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Página principal con el formulario de carga."""
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "messages": get_flashed_messages(with_categories=True)},
        )

    @app.post("/upload")
    async def upload_file(request: Request, file: UploadFile = File(...)):
        """Carga el archivo Excel, valida sus columnas y guarda la ruta en sesión."""
        try:
            # Verificar extensión
            if not file.filename.endswith(".xlsx"):
                flash.add_message("Formato de archivo no válido (debe ser .xlsx)", "error")
                return RedirectResponse(url="/", status_code=303)

            os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
            filename = secure_filename(file.filename)
            filepath = os.path.join(settings.UPLOAD_FOLDER, filename)

            contents = await file.read()
            with open(filepath, "wb") as f:
                f.write(contents)

            # Verificar que el archivo se guardó
            if not os.path.exists(filepath):
                flash.add_message("Error al guardar el archivo", "error")
                return RedirectResponse(url="/", status_code=303)

            # Verificar columnas requeridas
            try:
                df = pd.read_excel(filepath)
                required_columns = [
                    "Dominio",
                    "Workspace.Title",
                    "SelloNegocio",
                    "Titulo",
                    "DataOwner_Lgobierno",
                    "Responsable",
                ]
                missing_columns = [
                    col for col in required_columns if col not in df.columns
                ]
                if missing_columns:
                    flash.add_message(
                        f"Error: El archivo no contiene las columnas necesarias: {', '.join(missing_columns)}",
                        "error",
                    )
                    os.remove(filepath)  # Limpieza
                    return RedirectResponse(url="/", status_code=303)

            except ImportError as e:
                if "openpyxl" in str(e):
                    flash.add_message(
                        'Error: Falta la biblioteca openpyxl. Instale con "pip install openpyxl"',
                        "error",
                    )
                    os.remove(filepath)
                    return RedirectResponse(url="/", status_code=303)
                else:
                    raise

            # Guardar en sesión
            request.session.update(
                {
                    "current_file": filepath,
                    "current_step": "review",
                    "summary": {
                        "total_reports": len(df),
                        "pending_review": len(df[~df["SelloNegocio"]])
                        if "SelloNegocio" in df.columns
                        else 0,
                        "with_business_seal": len(df[df["SelloNegocio"]])
                        if "SelloNegocio" in df.columns
                        else 0,
                        "domains": df["Dominio"].nunique()
                        if "Dominio" in df.columns
                        else 0,
                        "data_owners": df["DataOwner_Lgobierno"].dropna().nunique()
                        if "DataOwner_Lgobierno" in df.columns
                        else 0,
                    },
                }
            )

            return RedirectResponse(url="/review", status_code=303)

        except Exception as e:
            flash.add_message(f"Error al procesar el archivo: {str(e)}", "error")
            return RedirectResponse(url="/", status_code=303)

    @app.get("/review")
    async def review_data(request: Request):
        """Muestra la vista previa de los reportes que no tienen SelloNegocio."""
        if "current_file" not in request.session:
            flash.add_message("No hay archivo para procesar", "error")
            return RedirectResponse(url="/", status_code=303)

        filepath = request.session["current_file"]
        if not os.path.exists(filepath):
            flash.add_message("El archivo ya no existe", "error")
            return RedirectResponse(url="/", status_code=303)

        try:
            df = pd.read_excel(filepath)
            df_pending = df[~df["SelloNegocio"]].sort_values(["Dominio", "Titulo"])

            reports = []
            for _, row in df_pending.iterrows():
                sello_list = []
                if "SelloTécnico" in df.columns and row.get("SelloTécnico"):
                    sello_list.append("Tecnología")
                if "SelloSeguridad" in df.columns and row.get("SelloSeguridad"):
                    sello_list.append("Seguridad")

                reports.append(
                    {
                        "dominio": format_empty_value(row["Dominio"]),
                        "titulo": clean_title(row["Titulo"]),
                        "workspace": format_empty_value(row["Workspace.Title"]),
                        "responsable": format_empty_value(row["Responsable"]),
                        "data_owner": format_empty_value(row["DataOwner_Lgobierno"]),
                        "sellos": " ; ".join(sello_list) if sello_list else "Sin sellos",
                    }
                )

            summary = {
                "filename": os.path.basename(filepath),
                "total_pending": len(df_pending),
                "reports": reports,
            }
            request.session["summary"] = summary

            return templates.TemplateResponse(
                "review.html", {"request": request, "summary": summary}
            )

        except Exception as e:
            flash.add_message(f"Error al procesar archivo: {str(e)}", "error")
            return RedirectResponse(url="/", status_code=303)

    @app.get("/confirm_send")
    @app.post("/confirm_send")
    async def confirm_send(request: Request):
        """Pantalla para confirmar el envío de correos."""
        if "current_file" not in request.session:
            flash.add_message("No hay archivo para procesar", "error")
            return RedirectResponse(url="/", status_code=303)

        try:
            _ = pd.read_excel(request.session["current_file"])  # Verificar lectura
            request.session["current_step"] = "send"
            return templates.TemplateResponse(
                "confirm_send.html",
                {"request": request, "current_step": "send"},
            )
        except Exception as e:
            flash.add_message(f"Error al procesar archivo: {str(e)}", "error")
            return RedirectResponse(url="/", status_code=303)

    @app.post("/process")
    async def process(request: Request, email_option: str = Form(...)):
        """Guarda la cuenta de correo elegida y redirige a la vista previa de correos."""
        if "current_file" not in request.session:
            flash.add_message("No hay archivo para procesar", "error")
            return RedirectResponse(url="/", status_code=303)

        try:
            request.session["email_option"] = email_option

            if email_option not in SMTP_CONFIG:
                flash.add_message("Opción de correo inválida", "error")
                return RedirectResponse(url="/confirm_send", status_code=303)

            return RedirectResponse(url="/preview_emails", status_code=303)

        except Exception as e:
            flash.add_message(f"Error en el procesamiento: {str(e)}", "error")
            return RedirectResponse(url="/", status_code=303)

    @app.get("/preview_emails")
    async def preview_emails(request: Request):
        """
        Muestra un resumen de a quién se enviarán los correos
        y cuántos reportes tiene cada Data Owner.
        """
        if "current_file" not in request.session:
            flash.add_message("No hay información para procesar", "error")
            return RedirectResponse(url="/", status_code=303)

        try:
            email_option = request.session.get("email_option")
            if not email_option:
                flash.add_message("Debe seleccionar una cuenta de correo", "error")
                return RedirectResponse(url="/confirm_send", status_code=303)

            df = pd.read_excel(request.session["current_file"])
            df_filtered = df[~df["SelloNegocio"]]

            if df_filtered["DataOwner_Lgobierno"].dropna().nunique() == 0:
                flash.add_message("No hay Data Owners válidos en el archivo.", "error")
                return RedirectResponse(url="/", status_code=303)

            preview_data = {
                "sender_email": SMTP_CONFIG[email_option][0],
                "account_type": SMTP_CONFIG[email_option][2],
                "recipients": {},
            }

            for _, row in df_filtered.iterrows():
                owner = row["DataOwner_Lgobierno"]
                if pd.notna(owner) and "@" in str(owner):
                    if owner not in preview_data["recipients"]:
                        preview_data["recipients"][owner] = []

                    preview_data["recipients"][owner].append(
                        {
                            "dominio": format_empty_value(row["Dominio"]),
                            "titulo": clean_title(row["Titulo"]),
                            "workspace": format_empty_value(row["Workspace.Title"]),
                            "responsable": format_empty_value(row["Responsable"]),
                            "sellos": formatear_sellos(row),
                        }
                    )

            return templates.TemplateResponse(
                "preview_emails.html", {"request": request, "preview": preview_data}
            )

        except Exception as e:
            flash.add_message(f"Error al generar vista previa: {str(e)}", "error")
            return RedirectResponse(url="/confirm_send", status_code=303)

    @app.post("/send_emails")
    async def send_emails(request: Request):
        """
        Envía los correos a cada DataOwner agrupado por dominio.
        Muestra un historial final con la información de envíos.
        """
        if "current_file" not in request.session:
            flash.add_message("No hay archivo para procesar", "error")
            return RedirectResponse(url="/", status_code=303)

        try:
            email_option = request.session.get("email_option")
            if not email_option:
                flash.add_message("Error: No se seleccionó cuenta de correo", "error")
                return RedirectResponse(url="/confirm_send", status_code=303)

            df = pd.read_excel(request.session["current_file"])
            df_filtered = df[~df["SelloNegocio"]]

            history_data = {
                "sender_email": SMTP_CONFIG[email_option][0],
                "account_type": SMTP_CONFIG[email_option][2],
                "total_sent": len(df_filtered["DataOwner_Lgobierno"].unique()),
                "total_reports": len(df_filtered),
                "total_domains": df_filtered["Dominio"].nunique(),
                "sent_emails": [],
            }

            # Llamar a la función asíncrona para enviar correos
            result, history_items = await process_excel_file(
                request.session["current_file"], email_option
            )

            if "Error" not in result:
                # Cargar la info de envíos
                for item in history_items:
                    history_data["sent_emails"].append(
                        {
                            "recipient": item["owner"],
                            "timestamp": item["timestamp"],
                            "reports": len(item["domain"]),
                            "domain_list": item["domain"],
                            "cc_count": len(item["domain"]),  # Ejemplo de conteo
                        }
                    )

                request.session["history_data"] = history_data
                return templates.TemplateResponse(
                    "history.html", {"request": request, "history": history_data}
                )

            flash.add_message(result, "error")
            return RedirectResponse(url="/", status_code=303)

        except Exception as e:
            flash.add_message(f"Error en el envío: {str(e)}", "error")
            return RedirectResponse(url="/", status_code=303)

        finally:
            # Limpieza final de sesión/archivo
            await cleanup_session(request)

    return app

# ================================
# Instanciar la aplicación
# ================================
app = create_app()

if __name__ == "__main__":
    # Evitar ejecución si faltan dependencias
    if missing_packages:
        sys.exit(1)

    import uvicorn

    # Agregar el directorio padre al path si es necesario
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    uvicorn.run(
        "sellos:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )

