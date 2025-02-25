import os
import pandas as pd
from fastapi import FastAPI, Request, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

# Actualizar la ruta de templates para usar templates_estado
template_dir = os.path.join(os.path.dirname(__file__), "templates_estado")
templates = Jinja2Templates(directory=template_dir)

# Verificar que el directorio de templates existe
if not os.path.exists(template_dir):
    os.makedirs(template_dir)
    print(f"Directorio de templates creado: {template_dir}")

# Carpeta donde se guardarán los archivos subidos
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Variable global para almacenar el DataFrame temporalmente
current_df = None

# Configuración SMTP
SMTP_CONFIG = {
    "1": ("cflores.practica@cmpc.com", "ywsb sfgz fmyf qdsg", "personal"),
    "2": ("datadriven@cmpc.cl", "ccgu zixq lzme xmsr", "DataDriven"),
}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Página de inicio con el formulario de carga."""
    messages = request.session.pop("messages", [])
    return templates.TemplateResponse(
        "index.html", {"request": request, "messages": messages}
    )


@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Estado de Reportes</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 40px;
            }
            h1 {
                color: #007bff;
            }
            .status-container {
                margin: 20px 0;
                padding: 20px;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
        </style>
    </head>
    <body>
        <h1>Estado de Reportes</h1>
        <div class="status-container">
            <h2>Estado Actual</h2>
            <p>Sistema funcionando correctamente</p>
        </div>
    </body>
    </html>
    """


@app.get("/status")
async def get_status():
    return {"status": "active", "message": "Sistema funcionando correctamente"}


@app.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Procesa la subida del archivo Excel."""
    global current_df

    if not file.filename.endswith(".xlsx"):
        request.session["messages"] = ["Por favor sube un archivo Excel (.xlsx)"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    try:
        # Leer el archivo especificando la hoja "Estado Reportes"
        content = await file.read()
        excel_file = pd.ExcelFile(content)

        # Verificar si existe la hoja "Estado Reportes"
        if "Estado Reportes" not in excel_file.sheet_names:
            request.session["messages"] = [
                "El archivo Excel no contiene la hoja 'Estado Reportes'"
            ]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        current_df = pd.read_excel(content, sheet_name="Estado Reportes")

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
            # Incluir todos los campos del DataFrame
            for column in current_df.columns:
                report_data[column.lower().replace(" ", "_")] = row.get(column, "")
            reports.append(report_data)

        summary = {
            "total_pending": len(reports),
            "columns": current_df.columns.tolist(),  # Incluir todas las columnas
            "reports": reports,
        }

        return templates.TemplateResponse(
            "preview.html", {"request": request, "summary": summary}
        )
    except Exception as e:
        request.session["messages"] = [f"Error al generar la vista previa: {str(e)}"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.get("/select_account", response_class=HTMLResponse)
async def select_account(request: Request):
    """Muestra la página de selección de cuenta."""
    # Convertir la configuración SMTP en el formato esperado por el template
    accounts = [
        {"id": id, "name": f"{config[0]} ({config[2]})"}
        for id, config in SMTP_CONFIG.items()
    ]

    return templates.TemplateResponse(
        "select_account.html", {"request": request, "accounts": accounts}
    )


@app.post("/process_account")
async def process_account(request: Request):
    """Procesa la selección de cuenta y continúa al siguiente paso."""
    form_data = await request.form()
    account_id = form_data.get("account")

    if not account_id or account_id not in SMTP_CONFIG:
        request.session["messages"] = ["Por favor seleccione una cuenta válida"]
        return RedirectResponse(
            url="/select_account", status_code=status.HTTP_302_FOUND
        )

    # Guardar la configuración SMTP seleccionada en la sesión
    email, password, account_type = SMTP_CONFIG[account_id]
    request.session["smtp_config"] = {
        "email": email,
        "password": password,
        "account_type": account_type,
    }

    return RedirectResponse(url="/confirm_send", status_code=status.HTTP_302_FOUND)


def process_preview_data(df):
    """Procesa el DataFrame para generar los datos agrupados por responsable."""
    preview_data = {}

    # Normalizar nombres de columnas
    df.columns = [col.strip().title() for col in df.columns]

    # Mapear columnas necesarias
    required_columns = {
        "responsable": next((col for col in df.columns if "Responsable" in col), None),
        "titulo": next(
            (col for col in df.columns if "Titulo" in col or "Título" in col), None
        ),
        "estado": next((col for col in df.columns if "Estado" in col), None),
        "fecha": next((col for col in df.columns if "Fecha" in col), None),
    }

    # Verificar que existan las columnas necesarias
    if not all(required_columns.values()):
        missing = [k for k, v in required_columns.items() if v is None]
        raise ValueError(f"Columnas faltantes: {', '.join(missing)}")

    # Agrupar por responsable
    for _, row in df.iterrows():
        responsable = row[required_columns["responsable"]]
        if pd.isna(responsable):
            responsable = "Sin Responsable Asignado"

        if responsable not in preview_data:
            preview_data[responsable] = []

        preview_data[responsable].append(
            {
                "titulo": row[required_columns["titulo"]],
                "estado": row[required_columns["estado"]],
                "responsable": responsable,
                "fecha_actualizacion": row[required_columns["fecha"]],
            }
        )

    return preview_data


def find_column(df, possible_names):
    """Encuentra una columna en el DataFrame basado en múltiples nombres posibles."""
    for col in df.columns:
        if any(name.lower() in col.lower() for name in possible_names):
            return col
    return None


def process_email_data(df):
    """Procesa el DataFrame para generar los datos agrupados por Data Owner y dominio, creando columnas dinámicas basadas en los estados de los reportes."""
    # Encontrar las columnas necesarias
    data_owner_col = find_column(df, ["Data Owner", "Owner"])
    dominio_col = find_column(df, ["Dominio"])
    endorsement_col = find_column(df, ["Endorsement"])
    visible_col = find_column(df, ["Visible"])
    titulo_col = find_column(df, ["Titulo", "Título"])

    if not all([data_owner_col, dominio_col, endorsement_col, visible_col, titulo_col]):
        print(f"Columnas disponibles: {df.columns.tolist()}")
        raise ValueError("Columnas faltantes para procesar los datos")

    # Agrupar por Data Owner y Dominio
    preview_emails = {}

    # Convertir columnas a formato adecuado
    df[endorsement_col] = df[endorsement_col].astype(str).str.lower()
    df[visible_col] = (
        df[visible_col].astype(str).str.lower().map({"true": True, "false": False})
    )

    for data_owner in df[data_owner_col].unique():
        if pd.isna(data_owner):
            continue

        owner_data = {}
        df_owner = df[df[data_owner_col] == data_owner]

        for dominio in df_owner[dominio_col].unique():
            if pd.isna(dominio):
                continue

            df_domain = df_owner[df_owner[dominio_col] == dominio]

            # Diccionario para almacenar combinaciones de estados
            estado_combinaciones = {}

            for _, row in df_domain.iterrows():
                titulo = row[titulo_col]

                # Determinar los estados pendientes
                pendientes = []

                # Verificar cada posible estado pendiente y agregar todos los aplicables

                # 1. Si el reporte está vacío o si no está ni promovido ni certificado
                if (
                    not any(
                        [pd.notna(row[col]) and row[col] != "" for col in df.columns]
                    )
                    or "promoted" not in row[endorsement_col]
                    and "certified" not in row[endorsement_col]
                ):
                    pendientes.append("por promocionar")

                # 2. Si el estado es 'promoted' o está en proceso de certificación
                if (
                    "promoted" in row[endorsement_col]
                    and "certified" not in row[endorsement_col]
                ):
                    pendientes.append("por certificar")

                # 3. Regla: Si visible es false, siempre debe estar como por publicar
                if not row[visible_col] and "por publicar" not in pendientes:
                    pendientes.append("por publicar")

                # Omitir reportes sin pendientes (certificados y publicados)
                if not pendientes:
                    continue

                # Crear clave combinada para el estado uniendo todos los estados pendientes
                clave_estado = " y ".join(sorted(set(pendientes)))

                # Agregar reporte a la combinación correspondiente
                if clave_estado not in estado_combinaciones:
                    estado_combinaciones[clave_estado] = []
                estado_combinaciones[clave_estado].append(titulo)

            # Crear tabla dinámica basada en combinaciones con estilo mejorado
            tabla_filas = ""
            for estado, reportes in estado_combinaciones.items():
                # Crear lista enumerada de reportes
                reportes_enumerados = [
                    f"{i + 1}. {reporte}" for i, reporte in enumerate(reportes)
                ]

                tabla_filas += f"""
                <tr>
                    <th style='text-align: left; padding: 8px; border: 1px solid #ddd; width: 30%; background-color: #ffffff; font-weight: bold; color: #1d8649;'>{estado.capitalize()}</th>
                    <td style='text-align: left; padding: 8px; border: 1px solid #ddd; width: 70%; background-color: #ffffff;'>{"<br>".join(reportes_enumerados)}</td>
                </tr>
                """

            if tabla_filas:
                owner_data[dominio] = {
                    "tabla_filas": tabla_filas,
                    "total_reportes": len(df_domain),
                    "reportes": df_domain.to_dict("records"),
                }

        if owner_data:
            preview_emails[data_owner] = owner_data

    return preview_emails


@app.get("/confirm_send", response_class=HTMLResponse)
async def confirm_send(request: Request):
    """Muestra la vista previa del correo antes de enviar."""
    global current_df

    if current_df is None:
        request.session["messages"] = ["No hay datos para enviar"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    smtp_config = request.session.get("smtp_config")
    if not smtp_config:
        request.session["messages"] = ["Por favor seleccione una cuenta de envío"]
        return RedirectResponse(
            url="/select_account", status_code=status.HTTP_302_FOUND
        )

    try:
        preview_data = process_email_data(current_df)
        preview_emails = {}

        # Generar vista previa para cada Data Owner
        for data_owner, dominios in preview_data.items():
            email_content = []

            for dominio, data in dominios.items():
                # Renderizar el template para cada dominio
                email_html = templates.get_template(
                    "email_template.html"
                ).render(
                    request=request,
                    dominio=dominio,
                    email=data_owner,
                    tabla_filas=data["tabla_filas"],
                    mensaje_total=f"Total de reportes: {data['total_reportes']}",  # Añadir esta línea
                )
                email_content.append(email_html)

            # Combinar todos los correos para este Data Owner
            preview_emails[data_owner] = "<hr/>".join(email_content)

        return templates.TemplateResponse(
            "confirm_send.html",
            {
                "request": request,
                "smtp_config": smtp_config,
                "preview_emails": preview_emails,
            },
        )

    except Exception as e:
        print(f"Error detallado: {str(e)}")
        import traceback

        print(traceback.format_exc())
        request.session["messages"] = [f"Error al generar la vista previa: {str(e)}"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.post("/send_emails")
async def send_emails(request: Request):
    """Envía los correos y muestra el resumen."""
    global current_df

    if current_df is None:
        request.session["messages"] = ["No hay datos para enviar"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    smtp_config = request.session.get("smtp_config")
    if not smtp_config:
        request.session["messages"] = ["Configuración de correo no encontrada"]
        return RedirectResponse(
            url="/select_account", status_code=status.HTTP_302_FOUND
        )

    try:
        preview_data = process_email_data(current_df)
        sent_emails = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 🔹 1. Establecer conexión SMTP UNA SOLA VEZ
        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.ehlo()
            server.starttls()
            server.ehlo()  # Segundo EHLO después de STARTTLS

            # Credenciales correctas
            email = smtp_config["email"].strip()
            password = smtp_config["password"].strip()

            print(f"Intentando conexión con: {email}")
            server.login(email, password)  # 📌 Iniciar sesión UNA SOLA VEZ
            print(f"Conexión SMTP exitosa con {email}")

        except smtplib.SMTPAuthenticationError as auth_error:
            print(f"Error de autenticación SMTP: {str(auth_error)}")
            request.session["messages"] = [
                "Error de autenticación. Verifique las credenciales."
            ]
            return RedirectResponse(
                url="/confirm_send", status_code=status.HTTP_302_FOUND
            )
        except Exception as smtp_error:
            print(f"Error de conexión SMTP: {str(smtp_error)}")
            request.session["messages"] = [
                "Error al conectar con el servidor de correo."
            ]
            return RedirectResponse(
                url="/confirm_send", status_code=status.HTTP_302_FOUND
            )

        # 🔹 2. Enviar correos a cada Data Owner usando la misma conexión
        for data_owner, dominios in preview_data.items():
            try:
                email_parts = []
                domains_list = []
                total_reports = 0

                for dominio, data in dominios.items():
                    domains_list.append(dominio)
                    total_reports += data["total_reportes"]

                    email_html = templates.get_template("email_template.html").render(
                        request=request,
                        dominio=dominio,
                        email=data_owner,
                        tabla_filas=data["tabla_filas"],
                    )
                    email_parts.append(email_html)

                # 🔹 Crear mensaje con codificación UTF-8
                msg = MIMEMultipart()
                msg["From"] = smtp_config["email"]
                msg["To"] = data_owner
                msg["Subject"] = f"Estado de Reportes - {', '.join(domains_list)}"

                # 🔹 Unir contenido HTML
                full_html = "<hr/>".join(email_parts)
                msg.attach(MIMEText(full_html, "html", "utf-8"))

                # 📌 Enviar mensaje utilizando la MISMA conexión SMTP
                server.send_message(msg)
                print(f"Correo enviado exitosamente a {data_owner}")

                sent_emails.append(
                    {
                        "recipient": data_owner,
                        "timestamp": timestamp,
                        "total_reports": total_reports,
                        "domains": domains_list,
                    }
                )

            except Exception as e:
                print(f"Error enviando correo a {data_owner}: {str(e)}")
                continue

            except Exception as e:
                print(f"Error enviando correo a {data_owner}: {str(e)}")
                continue

        # 🔹 3. Cerrar conexión SMTP una vez terminados todos los envíos
        server.quit()
        print("Conexión SMTP cerrada correctamente")

        if not sent_emails:
            raise Exception("No se pudo enviar ningún correo")

        # 🔹 4. Preparar historial
        history = {
            "sender_email": smtp_config["email"],
            "account_type": smtp_config["account_type"],
            "timestamp": timestamp,
            "total_recipients": len(sent_emails),
            "total_reports": sum(email["total_reports"] for email in sent_emails),
            "total_domains": len(
                set(domain for email in sent_emails for domain in email["domains"])
            ),
            "sent_emails": sent_emails,
        }

        return templates.TemplateResponse(
            "send_success.html", {"request": request, "history": history}
        )

    except Exception as e:
        print(f"Error enviando correos: {str(e)}")
        import traceback

        print(traceback.format_exc())
        request.session["messages"] = [f"Error al enviar los correos: {str(e)}"]
        return RedirectResponse(url="/confirm_send", status_code=status.HTTP_302_FOUND)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("estado:app", host="0.0.0.0", port=8000, reload=True)
