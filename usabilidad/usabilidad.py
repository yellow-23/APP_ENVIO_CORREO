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
import re
import logging
import uvicorn

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

# Actualizar la ruta de templates
template_dir = os.path.join(os.path.dirname(__file__), "templates")
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


@app.get("/preview_emails", response_class=HTMLResponse)
async def preview_emails(request: Request):
    """Muestra la vista previa de los correos a enviar."""
    if current_df is None:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # Debug: Mostrar las columnas disponibles y primeras filas
    print("Columnas disponibles:", current_df.columns.tolist())
    print("Primera fila del DataFrame:", current_df.iloc[0].to_dict())

    account_id = request.session.get("selected_account")
    if not account_id:
        return RedirectResponse(
            url="/select_account", status_code=status.HTTP_302_FOUND
        )

    # Verificar la columna de correos
    email_column = None
    for possible_column in ["Data Owner", "RESPONSABLE_EMAIL", "Email", "Correo"]:
        if possible_column in current_df.columns:
            email_column = possible_column
            break

    if not email_column:
        request.session["messages"] = [
            "No se encontró la columna de correos electrónicos"
        ]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # Preparar los correos para previsualización
    preview_emails = {}
    for email in current_df[email_column].dropna().unique():
        rows = current_df[current_df[email_column] == email]
        tabla_filas = ""

        # Obtener el área de datos del primer registro
        first_row = rows.iloc[0]
        print(f"Datos de fila para {email}:", dict(first_row))  # Debug

        # Usar los nombres exactos de las columnas como aparecen en el Excel
        area_datos = str(
            first_row.get(
                "Area Datos", first_row.get("area datos", "Área no especificada")
            )
        )

        for _, row in rows.iterrows():
            # Intentar diferentes variantes de nombres de columnas
            nombre_reporte = str(
                row.get(
                    "Nombre Reporte",
                    row.get(
                        "nombre reporte", row.get("Reporte", row.get("reporte", ""))
                    ),
                )
            )

            workspace = str(
                row.get("Workspace", row.get("workspace", row.get("WorkSpace", "")))
            )

            print(
                f"Fila procesada - Reporte: {nombre_reporte}, Workspace: {workspace}, Área: {area_datos}"
            )

            if nombre_reporte and workspace:  # Solo agregar si hay datos
                tabla_filas += f"""
                    <tr>
                        <td style="border: 1px solid #ddd; padding: 8px;">{nombre_reporte}</td>
                        <td style="border: 1px solid #ddd; padding: 8px;">{workspace}</td>
                    </tr>
                """

        # Crear el contenido del correo
        template_path = os.path.join(template_dir, "gmail_template.html")
        with open(template_path, "r", encoding="utf-8") as file:
            template = file.read()

        html_content = template.replace("{{ email }}", email)
        html_content = html_content.replace("{{ dominio }}", area_datos)
        html_content = html_content.replace("{{ tabla_filas|safe }}", tabla_filas)

        preview_emails[email] = html_content

    print(f"Correos a enviar: {list(preview_emails.keys())}")  # Debug

    return templates.TemplateResponse(
        "confirmacion_envio.html",
        {
            "request": request,
            "preview_emails": preview_emails,
            "smtp_config": SMTP_CONFIG[account_id],
        },
    )


async def send_email(smtp_config, to_emails, subject, html_content):
    """Envía un correo electrónico usando SMTP de Gmail."""
    try:
        msg = MIMEMultipart()
        msg["From"] = smtp_config["email"]
        msg["To"] = ", ".join(to_emails)
        msg["Subject"] = subject
        msg.attach(MIMEText(html_content, "html"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(smtp_config["email"], smtp_config["password"])
        server.sendmail(smtp_config["email"], to_emails, msg.as_string())
        server.quit()
        logger.info(f"Correo enviado a: {to_emails}")
        return True

    except Exception as e:
        logger.error(f"Error en envío de correo: {str(e)}")
        raise e


def get_email_domain(email):
    """Extrae el dominio de un correo electrónico."""
    match = re.search("@[\w.]+", email)
    return match.group() if match else "@unknown"


@app.post("/send_emails")
async def send_emails(request: Request):
    """Procesa el envío de los correos agrupados por dominio de correo."""
    try:
        if current_df is None:
            raise Exception("No hay datos cargados")

        account_id = request.session.get("selected_account")
        if not account_id:
            raise Exception("No se ha seleccionado una cuenta de envío")

        smtp_config = SMTP_CONFIG[account_id]

        # Verificar la columna de correos
        email_column = None
        for possible_column in ["Data Owner", "RESPONSABLE_EMAIL", "Email", "Correo"]:
            if possible_column in current_df.columns:
                email_column = possible_column
                break

        if not email_column:
            raise Exception("No se encontró la columna de correos electrónicos")

        # Verificar columnas necesarias
        required_columns = ["Data Owner", "Data Steward", "Nombre Reporte", "Workspace"]
        missing_columns = [
            col for col in required_columns if col not in current_df.columns
        ]
        if missing_columns:
            raise Exception(
                f"Faltan las siguientes columnas requeridas en el archivo: {missing_columns}"
            )

        history = {
            "sent_emails": [],
            "errors": [],
            "total_domains": 0,
            "total_recipients": 0,
        }

        # Obtener todos los correos únicos y sus dominios
        unique_emails = pd.concat(
            [
                current_df["Data Owner"].dropna(),
                current_df["Data Steward"].dropna(),
            ]
        ).unique()

        # Procesar cada correo
        for email in unique_emails:
            try:
                print(f"\nProcesando envío para: {email}")  # Debug print

                # Filtrar registros para este correo
                rows = current_df[current_df[email_column] == email]
                print(f"Registros encontrados para {email}: {len(rows)}")  # Debug print

                # Generar contenido
                tabla_filas = ""

                # Obtener el dominio directamente del correo
                domain = get_email_domain(email)
                area_datos = domain  # Usar el dominio en lugar de "área datos"

                for _, row in rows.iterrows():
                    row_dict = row.to_dict()
                    # Usar los nombres exactos de las columnas
                    nombre_reporte = str(row_dict.get("nombre reporte", "")) or str(
                        row_dict.get("reporte", "")
                    )
                    workspace = str(row_dict.get("workspace", ""))

                    tabla_filas += f"""
                        <tr>
                            <td style="border: 1px solid #ddd; padding: 8px;">{nombre_reporte}</td>
                            <td style="border: 1px solid #ddd; padding: 8px;">{workspace}</td>
                        </tr>
                    """

                # Crear el contenido del correo
                template_path = os.path.join(template_dir, "gmail_template.html")
                with open(template_path, "r", encoding="utf-8") as file:
                    template = file.read()

                html_content = template.replace("{{ email }}", email)
                html_content = html_content.replace("{{ dominio }}", area_datos)
                html_content = html_content.replace(
                    "{{ tabla_filas|safe }}", tabla_filas
                )

                # Intentar envío
                subject = f"Reporte CMPC - {datetime.now().strftime('%d/%m/%Y')} - {area_datos}"
                await send_email(smtp_config, [email], subject, html_content)

                # Registrar éxito
                history["sent_emails"].append(
                    {
                        "recipient": email,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "total_reports": len(rows),
                        "domains": [domain],
                    }
                )

            except Exception as e:
                error_msg = f"Error enviando a {email}: {str(e)}"
                print(f"Error en envío: {error_msg}")  # Debug print
                history["errors"].append(error_msg)

        # Actualizar estadísticas
        history["total_domains"] = len(
            set(email["domains"][0] for email in history["sent_emails"])
        )
        history["total_recipients"] = len(history["sent_emails"])

        # Guardar historial en la sesión
        request.session["send_history"] = history

        if history["errors"]:
            request.session["messages"] = [
                "Proceso completado con algunos errores. Revise el historial para más detalles."
            ]
        else:
            request.session["messages"] = ["Correos enviados correctamente por dominio"]

        return RedirectResponse(
            url="/envio_realizado", status_code=status.HTTP_302_FOUND
        )

    except Exception as e:
        logger.error(f"Error general en envío de correos: {str(e)}")
        request.session["messages"] = [f"Error en envío: {str(e)}"]
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
