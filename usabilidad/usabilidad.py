import os
import pandas as pd
from fastapi import FastAPI, Request, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import logging
import uvicorn

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

# Actualizar la ruta de templates
template_dir = os.path.join(os.path.dirname(__file__), "templates_usabilidad")
templates = Jinja2Templates(directory=template_dir)

# Carpeta donde se guardarán los archivos subidos
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Verificar y crear el directorio 'static'
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

# Montar el directorio estático
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Variable global para almacenar el DataFrame temporalmente
current_df = None

# Actualizar configuración SMTP con información para Gmail
SMTP_CONFIG = {
    "1": {
        "email": "mmunozp.practica@cmpc.com",  # Cambiar a tu correo Gmail
        "password": "hlpi nude axco cwme",
        "account_type": "Cuenta de Pruebas",
        "description": "Use esta cuenta para pruebas iniciales",
    }
}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Página de inicio con el formulario de carga."""
    messages = request.session.pop("messages", [])
    return templates.TemplateResponse(
        "intro.html",  # Cambiado de index.html a intro.html
        {"request": request, "messages": messages},
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Procesa la subida del archivo Excel."""
    global current_df

    if not file.filename.endswith(".xlsx"):
        request.session["messages"] = ["Por favor sube un archivo Excel (.xlsx)"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    try:
        # Leer el archivo especificando la hoja "Hoja 1"
        content = await file.read()
        current_df = pd.read_excel(content, sheet_name="Hoja 1")
        current_df.columns = current_df.columns.str.strip()

        # Redirigir a la vista previa
        return RedirectResponse(url="/preview", status_code=status.HTTP_302_FOUND)

    except Exception as e:
        request.session["messages"] = [f"Error al procesar el archivo: {str(e)}"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.get("/preview", response_class=HTMLResponse)
async def preview(request: Request):
    """Muestra una vista previa del Excel procesado."""
    global current_df

    if current_df is None:
        request.session["messages"] = ["No hay archivo cargado para previsualizar"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    try:
        # Preparar los datos para la plantilla
        reports = []
        for _, row in current_df.iterrows():
            report_data = {}
            for column in current_df.columns:
                report_data[column.lower().replace(" ", "_")] = row.get(column, "")
            reports.append(report_data)

        summary = {
            "total_pending": len(reports),
            "columns": current_df.columns.tolist(),
            "reports": reports,  # Agregado para coincidir con el template
        }

        return templates.TemplateResponse(
            "vista_preview.html",  # Cambiado a vista_preview.html
            {"request": request, "summary": summary},
        )

    except Exception as e:
        request.session["messages"] = [f"Error al mostrar la vista previa: {str(e)}"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.get("/select_account", response_class=HTMLResponse)
async def select_account(request: Request):
    """Página de selección de cuenta."""
    if current_df is None:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    messages = request.session.pop("messages", [])
    return templates.TemplateResponse(
        "seleccionar_cuenta.html",
        {"request": request, "messages": messages, "accounts": SMTP_CONFIG},
    )


@app.post("/process_account")
async def process_account(request: Request):
    """Procesa la selección de cuenta."""
    try:
        form_data = await request.form()
        account_id = form_data.get("account")

        if account_id not in SMTP_CONFIG:
            request.session["messages"] = ["Cuenta inválida seleccionada"]
            return RedirectResponse(
                url="/select_account", status_code=status.HTTP_302_FOUND
            )

        # Guardar la cuenta seleccionada en la sesión
        request.session["selected_account"] = account_id

        # Redirigir a la vista previa de correos
        return RedirectResponse(
            url="/preview_emails", status_code=status.HTTP_302_FOUND
        )

    except Exception as e:
        request.session["messages"] = [f"Error al procesar la selección: {str(e)}"]
        return RedirectResponse(
            url="/select_account", status_code=status.HTTP_302_FOUND
        )


def generate_email_html(row_data):
    """Genera el HTML para el correo usando el template de Gmail."""
    try:
        # Obtener el email y el área de datos (dominio)
        email = (
            row_data.get("data_owner")
            or row_data.get("responsable_email")
            or row_data.get("email")
            or "Usuario"
        )
        dominio = (
            row_data.get("area_datos")
            or row_data.get("dominio")
            or "Área no especificada"
        )

        # Generar fila de tabla con los datos del reporte
        tabla_filas = ""
        if "nombre_reporte" in row_data and "workspace" in row_data:
            tabla_filas = f"""
                <tr>
                    <td style="border: 1px solid #ddd; padding: 8px;">{row_data["nombre_reporte"]}</td>
                    <td style="border: 1px solid #ddd; padding: 8px;">{row_data["workspace"]}</td>
                </tr>
            """

        # Debugging: imprimir datos disponibles
        print("Datos disponibles:", row_data.keys())
        print(f"Reporte: {row_data.get('nombre_reporte', 'No encontrado')}")
        print(f"Workspace: {row_data.get('workspace', 'No encontrado')}")
        print(f"Dominio/Área: {dominio}")

        # Leer el template
        template_path = os.path.join(template_dir, "gmail_template.html")
        with open(template_path, "r", encoding="utf-8") as file:
            template = file.read()

        # Reemplazar los placeholders
        html_content = template.replace("{{ email }}", email)
        html_content = template.replace("{{ dominio }}", dominio)
        html_content = template.replace("{{ tabla_filas|safe }}", tabla_filas)

        return html_content

    except Exception as e:
        logger.error(f"Error generando HTML: {str(e)}")
        return f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2>Error generando el template</h2>
            <p>Se encontró un error al generar el contenido del correo.</p>
            <p>Error: {str(e)}</p>
        </div>
        """


def clean_area_name(area):
    """Limpia el nombre del área eliminando la palabra 'Área' al inicio."""
    area = str(area).strip()
    if area.lower().startswith("área "):
        area = area[5:].strip()
    elif area.lower().startswith("area "):
        area = area[5:].strip()
    return area

async def send_email(smtp_config, recipient, subject, html_content):
    """Send email using the specified SMTP configuration."""
    msg = MIMEMultipart()
    msg['From'] = smtp_config['email']
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.attach(MIMEText(html_content, 'html'))

    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(smtp_config['email'], smtp_config['password'])
        server.send_message(msg)


@app.get("/preview_emails", response_class=HTMLResponse)
async def preview_emails(request: Request):
    if current_df is None:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    account_id = request.session.get("selected_account")
    if not account_id:
        return RedirectResponse(
            url="/select_account", status_code=status.HTTP_302_FOUND
        )

    # Verificar la columna de correos y área datos
    email_column = None
    area_column = None

    for possible_column in ["Data Owner", "RESPONSABLE_EMAIL", "Email", "Correo"]:
        if possible_column in current_df.columns:
            email_column = possible_column
            break

    for possible_column in ["Area datos", "Area Datos", "area datos"]:
        if possible_column in current_df.columns:
            area_column = possible_column
            break

    if not email_column or not area_column:
        request.session["messages"] = ["No se encontraron las columnas necesarias"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # Agrupar por área de datos
    preview_emails = {}
    for area in current_df[area_column].dropna().unique():
        area_datos = clean_area_name(str(area))
        area_rows = current_df[current_df[area_column] == area]

        # Procesar cada correo dentro del área
        for email in area_rows[email_column].dropna().unique():
            email_rows = area_rows[area_rows[email_column] == email]
            tabla_filas = ""

            for _, row in email_rows.iterrows():
                nombre_reporte = str(row.get("Nombre Reporte", row.get("Reporte", "")))
                workspace = str(row.get("Workspace", ""))

                if nombre_reporte and workspace:
                    tabla_filas += f"""
                        <tr>
                            <td style="border: 1px solid #ddd; padding: 8px;">{nombre_reporte}</td>
                            <td style="border: 1px solid #ddd; padding: 8px;">{workspace}</td>
                        </tr>
                    """

            if tabla_filas:
                key = f"{area_datos}_{email}"
                template_path = os.path.join(template_dir, "gmail_template.html")
                with open(template_path, "r", encoding="utf-8") as file:
                    template = file.read()

                html_content = template.replace("{{ email }}", email)
                html_content = html_content.replace("{{ dominio }}", area_datos)
                html_content = html_content.replace(
                    "{{ tabla_filas|safe }}", tabla_filas
                )
                preview_emails[key] = html_content

    return templates.TemplateResponse(
        "confirmacion_envio.html",
        {
            "request": request,
            "preview_emails": preview_emails,
            "smtp_config": SMTP_CONFIG[account_id],
        },
    )


@app.post("/send_emails")
async def send_emails(request: Request):
    try:
        if current_df is None:
            raise Exception("No hay datos cargados")

        # Verificar columnas necesarias
        email_column = None
        area_column = None

        for possible_column in ["Data Owner", "RESPONSABLE_EMAIL", "Email", "Correo"]:
            if possible_column in current_df.columns:
                email_column = possible_column
                break

        for possible_column in ["Area datos", "Area Datos", "area datos"]:
            if possible_column in current_df.columns:
                area_column = possible_column
                break

        if not email_column or not area_column:
            raise Exception("No se encontraron las columnas necesarias")

        account_id = request.session.get("selected_account")
        if not account_id:
            raise Exception("No se ha seleccionado una cuenta de envío")

        smtp_config = SMTP_CONFIG[account_id]
        history = {
            "sender_email": smtp_config["email"],
            "account_type": smtp_config["account_type"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sent_emails": [],
            "errors": [],
            "total_recipients": 0,
            "total_reports": 0,
            "total_domains": 0,
        }

        # Procesar cada área
        for area in current_df[area_column].dropna().unique():
            area_datos = clean_area_name(str(area))
            area_rows = current_df[current_df[area_column] == area]

            # Procesar cada correo dentro del área
            for email in area_rows[email_column].dropna().unique():
                try:
                    email_rows = area_rows[area_rows[email_column] == email]
                    tabla_filas = ""

                    for _, row in email_rows.iterrows():
                        nombre_reporte = str(
                            row.get("Nombre Reporte", row.get("Reporte", ""))
                        )
                        workspace = str(row.get("Workspace", ""))

                        if nombre_reporte and workspace:
                            tabla_filas += f"""
                                <tr>
                                    <td style="border: 1px solid #ddd; padding: 8px;">{nombre_reporte}</td>
                                    <td style="border: 1px solid #ddd; padding: 8px;">{workspace}</td>
                                </tr>
                            """

                    if tabla_filas:
                        template_path = os.path.join(
                            template_dir, "gmail_template.html"
                        )
                        with open(template_path, "r", encoding="utf-8") as file:
                            template = file.read()

                        html_content = template.replace("{{ email }}", email)
                        html_content = html_content.replace("{{ dominio }}", area_datos)
                        html_content = html_content.replace(
                            "{{ tabla_filas|safe }}", tabla_filas
                        )

                        subject = f"Reporte CMPC - {datetime.now().strftime('%d/%m/%Y')} - {area_datos}"
                        await send_email(smtp_config, email, subject, html_content)

                        history["sent_emails"].append(
                            {
                                "recipient": email,
                                "area": area_datos,
                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                "total_reports": len(email_rows),
                            }
                        )

                except Exception as e:
                    error_msg = (
                        f"Error enviando a {email} (Área: {area_datos}): {str(e)}"
                    )
                    history["errors"].append(error_msg)

        # Actualizar estadísticas
        history["total_recipients"] = len(history["sent_emails"])
        history["total_reports"] = sum(
            email["total_reports"] for email in history["sent_emails"]
        )
        history["total_domains"] = len(
            set(email["area"] for email in history["sent_emails"])
        )

        request.session["send_history"] = history
        return RedirectResponse(
            url="/envio_realizado", status_code=status.HTTP_302_FOUND
        )

    except Exception as e:
        error_msg = f"Error general: {str(e)}"
        request.session["messages"] = [error_msg]
        return RedirectResponse(
            url="/preview_emails", status_code=status.HTTP_302_FOUND
        )


@app.get("/envio_realizado", response_class=HTMLResponse)
async def envio_realizado(request: Request):
    """Muestra el resumen del envío de correos."""
    history = request.session.get("send_history")
    if not history:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        "envio_realizado.html", {"request": request, "history": history}
    )


# Crear la aplicación
def create_app():
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
