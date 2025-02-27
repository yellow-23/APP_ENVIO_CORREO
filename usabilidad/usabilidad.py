import os
import re
import logging
import pandas as pd
from datetime import datetime
from fastapi import FastAPI, Request, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# =========================================
# Configuración de logging
# =========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================================
# Configuración SMTP de ejemplo
# =========================================
SMTP_CONFIG = {
    "1": {
        "email": "mmunozp.practica@cmpc.com",
        "password": "hlpi nude axco cwme",
        "account_type": "Cuenta de Pruebas",
        "description": "Use esta cuenta para pruebas iniciales",
    }
}

# =========================================
# Función para extraer el dominio de un email
# =========================================
def get_email_domain(email: str) -> str:
    """Extrae el dominio de un correo electrónico."""
    match = re.search(r"@[\w.]+", email)
    return match.group() if match else "@unknown"

# =========================================
# Creación de la aplicación vía Factory
# =========================================
def create_app() -> FastAPI:
    """
    Crea y configura la aplicación FastAPI para la gestión de usabilidad.
    """
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

    # Directorios de templates y estáticos
    base_dir = os.path.dirname(os.path.abspath(__file__))
    template_dir = os.path.join(base_dir, "templates")
    static_dir = os.path.join(base_dir, "static")
    os.makedirs(static_dir, exist_ok=True)

    templates = Jinja2Templates(directory=template_dir)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Carpeta de uploads
    upload_folder = os.path.join(base_dir, "uploads")
    os.makedirs(upload_folder, exist_ok=True)

    # Variable global para almacenar el DataFrame
    # (Para un sistema productivo, se recomienda una solución más robusta.)
    global current_df
    current_df = None

    # ===============================
    # Rutas
    # ===============================

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """
        Pantalla de inicio con formulario de carga.
        Muestra mensajes en sesión (si los hay).
        """
        messages = request.session.pop("messages", [])
        return templates.TemplateResponse(
            "intro.html",
            {"request": request, "messages": messages},
        )

    @app.post("/upload", response_class=HTMLResponse)
    async def upload_file(request: Request, file: UploadFile = File(...)):
        """
        Procesa la subida de un archivo Excel, esperando que la hoja se llame 'Hoja 1'.
        El DataFrame se almacena en 'current_df'.
        """
        global current_df

        if not file.filename.endswith(".xlsx"):
            request.session["messages"] = ["Por favor sube un archivo Excel (.xlsx)"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        try:
            content = await file.read()
            df = pd.read_excel(content, sheet_name="Hoja 1")
            df.columns = df.columns.str.strip()  # Limpia espacios en nombres de columnas

            current_df = df
            logger.info(f"Archivo Excel cargado correctamente con {len(df)} filas.")

            # Redirige a la vista previa
            return RedirectResponse(url="/preview", status_code=status.HTTP_302_FOUND)

        except Exception as e:
            logger.error(f"Error al procesar el archivo: {str(e)}")
            request.session["messages"] = [f"Error al procesar el archivo: {str(e)}"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    @app.get("/preview", response_class=HTMLResponse)
    async def preview(request: Request):
        """
        Muestra una vista previa de todos los datos cargados en 'current_df'.
        """
        global current_df

        if current_df is None:
            request.session["messages"] = ["No hay archivo cargado para previsualizar"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        try:
            # Convertir cada fila del DataFrame a un dict
            reports = []
            for _, row in current_df.iterrows():
                row_data = {}
                for column in current_df.columns:
                    # Se renombra la columna para la plantilla
                    row_data[column.lower().replace(" ", "_")] = row.get(column, "")
                reports.append(row_data)

            summary = {
                "total_pending": len(reports),
                "columns": current_df.columns.tolist(),
                "reports": reports,
            }

            return templates.TemplateResponse(
                "vista_preview.html",
                {"request": request, "summary": summary},
            )

        except Exception as e:
            logger.error(f"Error al mostrar la vista previa: {str(e)}")
            request.session["messages"] = [f"Error al mostrar la vista previa: {str(e)}"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    @app.get("/select_account", response_class=HTMLResponse)
    async def select_account(request: Request):
        """
        Permite seleccionar la cuenta SMTP para enviar correos.
        """
        global current_df
        if current_df is None:
            logger.info("No hay archivo cargado; redirigiendo a /")
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        messages = request.session.pop("messages", [])
        return templates.TemplateResponse(
            "seleccionar_cuenta.html",
            {"request": request, "messages": messages, "accounts": SMTP_CONFIG},
        )

    @app.post("/process_account")
    async def process_account(request: Request):
        """
        Procesa la selección de la cuenta SMTP y guarda la opción en la sesión.
        """
        try:
            form_data = await request.form()
            account_id = form_data.get("account")

            if account_id not in SMTP_CONFIG:
                request.session["messages"] = ["Cuenta inválida seleccionada"]
                return RedirectResponse(
                    url="/select_account", status_code=status.HTTP_302_FOUND
                )

            request.session["selected_account"] = account_id
            return RedirectResponse(
                url="/preview_emails", status_code=status.HTTP_302_FOUND
            )

        except Exception as e:
            logger.error(f"Error al procesar la selección de cuenta: {str(e)}")
            request.session["messages"] = [f"Error al procesar la cuenta: {str(e)}"]
            return RedirectResponse(
                url="/select_account", status_code=status.HTTP_302_FOUND
            )

    @app.get("/preview_emails", response_class=HTMLResponse)
    async def preview_emails(request: Request):
        """
        Muestra una vista previa de los correos que se van a enviar,
        agrupando por dirección de correo.
        """
        global current_df
        if current_df is None:
            request.session["messages"] = ["No hay datos cargados para previsualizar"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        account_id = request.session.get("selected_account")
        if not account_id:
            request.session["messages"] = ["No se ha seleccionado una cuenta de envío"]
            return RedirectResponse(url="/select_account", status_code=status.HTTP_302_FOUND)

        # Buscar columna que contenga emails (ejemplos)
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

        # Verificar la columna de Area datos
        if "Area datos" not in current_df.columns:
            request.session["messages"] = [
                "No se encontró la columna 'Area datos' requerida"
            ]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        preview_data = []  # Lista de diccionarios para pasar a la plantilla

        # Agrupar los datos por área de datos y email 
        current_df["Area datos"] = current_df["Area datos"].astype(str).str.strip()
        grouped = current_df.groupby(["Area datos", email_column], dropna=True)
        
        for (area_datos, email), rows in grouped:
            # Obtener la "Área" o "Dominio" si existe
            area_datos = area_datos.strip() if isinstance(area_datos, str) else "Área no especificada"
            logger.info(f"Área de datos asignada para {email}: {area_datos}")
            
            # Construir filas de tabla
            tabla_filas = ""
            for _, row in rows.iterrows():
                reporte = str(
                    row.get("Nombre Reporte", row.get("nombre reporte", row.get("Reporte", "")))
                )
                workspace = str(row.get("Workspace", row.get("workspace", "")))
                if reporte or workspace:
                    tabla_filas += f"""
                        <tr>
                            <td style="border: 1px solid #ddd; padding: 8px;">{reporte}</td>
                            <td style="border: 1px solid #ddd; padding: 8px;">{workspace}</td>
                        </tr>
                    """

            # Leer la plantilla base
            template_path = os.path.join(template_dir, "gmail_template.html")
            with open(template_path, "r", encoding="utf-8") as file:
                template = file.read()

            # Crear el HTML renderizado para este correo
            contenido_html = template.replace("{{ tabla_filas|safe }}", tabla_filas)
            contenido_html = contenido_html.replace("{{ email }}", str(email))
            contenido_html = contenido_html.replace("{{ area_datos }}", area_datos)

            # Agregar a la lista de previsualizaciones
            preview_data.append({
                "email": email,
                "area_datos": area_datos,
                "tabla_filas": tabla_filas,
                "contenido_html": contenido_html,
                "num_reportes": len(rows)
            })
                    
        smtp_data = SMTP_CONFIG[account_id]
        
        # Si no hay datos para previsualizar
        if not preview_data:
            request.session["messages"] = ["No se encontraron datos válidos para enviar correos"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        
        # Mostrar la vista previa de los correos en lugar de redireccionar
        return templates.TemplateResponse(
            "confirmacion_envio.html",
            {
                "request": request,
                "preview_emails": preview_data,
                "smtp_config": smtp_data,
                "total_emails": len(preview_data)
            },
        )

    async def send_email(smtp_config: dict, to_emails: list, subject: str, html_content: str):
        """
        Envía un correo electrónico usando SMTP (Gmail).
        """
        try:
            msg = MIMEMultipart()
            msg["From"] = smtp_config["email"]
            msg["To"] = ", ".join(to_emails)
            msg["Subject"] = subject
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(smtp_config["email"], smtp_config["password"])
                server.sendmail(smtp_config["email"], to_emails, msg.as_string())

            logger.info(f"Correo enviado a: {to_emails}")
            return True

        except Exception as e:
            logger.error(f"Error en envío de correo a {to_emails}: {str(e)}")
            raise e

    # Corregir el decorador para usar get y post por separado
    @app.post("/send_emails")  # Solo permitir POST para enviar correos
    async def send_emails_route(request: Request):
        """
        Envía los correos para cada email encontrado en el DataFrame,
        agrupando la información y registrando en un historial.
        """
        global current_df
        if current_df is None:
            request.session["messages"] = ["No hay datos cargados para enviar"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        account_id = request.session.get("selected_account")
        if not account_id:
            request.session["messages"] = ["No se ha seleccionado una cuenta de envío"]
            return RedirectResponse(url="/select_account", status_code=status.HTTP_302_FOUND)

        smtp_config = SMTP_CONFIG[account_id]
        
        # Identificar la columna de email (simplificado para evitar redirecciones)
        email_column = None
        for possible_column in ["Data Owner", "RESPONSABLE_EMAIL", "Email", "Correo"]:
            if possible_column in current_df.columns:
                email_column = possible_column
                break
        
        if not email_column:
            request.session["messages"] = ["No se encontró la columna de correos electrónicos"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        # Historial de envíos
        history = {
            "sent_emails": [],
            "errors": [],
            "total_domains": 0,
            "total_recipients": 0,
        }

        # Unir Data Owner y Data Steward para sacar lista de correos
        try:
            if "Data Owner" in current_df.columns and "Data Steward" in current_df.columns:
                unique_emails = pd.concat(
                    [current_df["Data Owner"].dropna(), current_df["Data Steward"].dropna()]
                ).unique()
            else:
                unique_emails = current_df[email_column].dropna().unique()
        except Exception as e:
            logger.error(f"Error al obtener lista de correos: {str(e)}")
            request.session["messages"] = [f"Error al procesar los correos: {str(e)}"]
            return RedirectResponse(url="/preview_emails", status_code=status.HTTP_302_FOUND)

        for email in unique_emails:
            try:
                # Filtrar las filas de este correo
                df_filtered = current_df[current_df[email_column] == email]
                if df_filtered.empty:
                    continue

                domain = get_email_domain(str(email))
                tabla_filas = ""

                for _, row in df_filtered.iterrows():
                    reporte = str(row.get("Nombre Reporte", row.get("Reporte", "")))
                    workspace = str(row.get("Workspace", ""))
                    tabla_filas += f"""
                        <tr>
                            <td style="border: 1px solid #ddd; padding: 8px;">{reporte}</td>
                            <td style="border: 1px solid #ddd; padding: 8px;">{workspace}</td>
                        </tr>
                    """

                # Renderizar template
                template_path = os.path.join(template_dir, "gmail_template.html")
                with open(template_path, "r", encoding="utf-8") as file:
                    template_html = file.read()

                html_content = template_html.replace("{{ email }}", str(email))
                html_content = html_content.replace("{{ dominio }}", domain)
                html_content = html_content.replace("{{ tabla_filas|safe }}", tabla_filas)

                subject = f"Reporte CMPC - {datetime.now().strftime('%d/%m/%Y')} - {domain}"
                await send_email(smtp_config, [email], subject, html_content)

                history["sent_emails"].append(
                    {
                        "recipient": email,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "total_reports": len(df_filtered),
                        "domains": [domain],
                    }
                )

            except Exception as e:
                error_msg = f"Error enviando a {email}: {str(e)}"
                logger.error(error_msg)
                history["errors"].append(error_msg)

        # Estadísticas finales
        history["total_domains"] = len({dom for item in history["sent_emails"] for dom in item["domains"]})
        history["total_recipients"] = len(history["sent_emails"])

        request.session["send_history"] = history
        if history["errors"]:
            request.session["messages"] = [
                "Proceso completado con algunos errores. Revise el historial."
            ]
        else:
            request.session["messages"] = ["Correos enviados exitosamente."]

        return RedirectResponse(url="/envio_realizado", status_code=status.HTTP_302_FOUND)

    @app.get("/envio_realizado", response_class=HTMLResponse)
    async def envio_realizado(request: Request):
        """
        Muestra un resumen del envío de correos en la plantilla correspondiente.
        """
        history = request.session.get("send_history")
        if not history:
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        return templates.TemplateResponse(
            "envio_realizado.html",
            {"request": request, "history": history},
        )

    return app


# =========================================
# Ejecución principal
# =========================================
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
