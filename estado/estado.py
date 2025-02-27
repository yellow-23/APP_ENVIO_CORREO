import os
import pandas as pd
import smtplib
import traceback
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import FastAPI, Request, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import uvicorn

# ===============================================
# Configuración de la aplicación
# ===============================================
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

# Directorio de templates
template_dir = os.path.join(os.path.dirname(__file__), "templates_estado")
os.makedirs(template_dir, exist_ok=True)
templates = Jinja2Templates(directory=template_dir)

# Configuración SMTP (ID -> (Correo, Contraseña, Descripción))
SMTP_CONFIG = {
    "1": ("cflores.practica@cmpc.com", "ywsb sfgz fmyf qdsg", "personal"),
    "2": ("datadriven@cmpc.cl", "ccgu zixq lzme xmsr", "DataDriven"),
}

# Variable global para almacenar temporalmente el DataFrame cargado
current_df = None


# ===============================================
# Funciones de utilidad
# ===============================================
def find_column(df: pd.DataFrame, possible_names: list) -> str:
    """
    Encuentra una columna en el DataFrame basada en múltiples nombres posibles.
    Retorna el nombre de la columna si se encuentra, de lo contrario None.
    """
    for col in df.columns:
        if any(name.lower() in col.lower() for name in possible_names):
            return col
    return None


def process_email_data(df: pd.DataFrame) -> dict:
    """
    Procesa el DataFrame para generar datos agrupados por Data Owner y Dominio.
    Crea columnas dinámicas basadas en los estados de los reportes según:
      - Publicado => "Promocionado"
      - 3 Sellos (Seguridad, Negocio, Tecnología) => "Certificado"
      - Si no está publicado => "por publicar"
      - Si está publicado pero no tiene los 3 sellos => "por certificar"
    """
    # Encuentra las columnas necesarias
    data_owner_col = find_column(df, ["Data Owner", "Owner"])
    dominio_col = find_column(df, ["Dominio"])
    visible_col = find_column(df, ["Visible"])
    titulo_col = find_column(df, ["Titulo", "Título"])
    sello_col = find_column(df, ["Sello", "Sellos"])  # Ajusta según tu Excel

    if not all([data_owner_col, dominio_col, visible_col, titulo_col, sello_col]):
        print(f"Columnas disponibles: {df.columns.tolist()}")
        raise ValueError("Faltan columnas necesarias para procesar los datos")

    # Normalizar la columna "Visible"
    df[visible_col] = df[visible_col].astype(str).str.lower().map({"true": True, "false": False})

    # Diccionario para el preview final
    preview_emails = {}

    # Agrupar por Data Owner y Dominio
    for data_owner in df[data_owner_col].unique():
        if pd.isna(data_owner):
            continue
        owner_data = {}
        df_owner = df[df[data_owner_col] == data_owner]

        for dominio in df_owner[dominio_col].unique():
            if pd.isna(dominio):
                continue
            df_domain = df_owner[df_owner[dominio_col] == dominio]

            estado_combinaciones = {}
            for _, row in df_domain.iterrows():
                titulo = row[titulo_col]
                pendientes = []

                # 1) Está publicado => lo consideramos "promocionado"
                #    Si no está publicado => "por publicar"
                is_published = row[visible_col]  # True/False

                # 2) Verificamos si tiene los 3 sellos
                #    Suponiendo que la columna Sello contenga algo como:
                #    "Seguridad, Negocio, Tecnología" o parcialmente
                if pd.notna(row[sello_col]):
                    sellos_lower = [s.strip().lower() for s in str(row[sello_col]).split(",")]
                    has_3_sellos = all(sello in sellos_lower for sello in ["seguridad", "negocio", "tecnología"])
                else:
                    has_3_sellos = False

                # Lógica de pendientes:
                # Si NO está publicado => "por publicar"
                if not is_published:
                    pendientes.append("por publicar")
                else:
                    # Está publicado => se asume "promocionado"
                    # Ahora, si no tiene los 3 sellos => "por certificar"
                    if not has_3_sellos:
                        pendientes.append("por certificar")
                    # Si tiene los 3 sellos => ya está certificado, entonces sin pendientes

                # Omitimos los reportes que no tengan pendientes
                if not pendientes:
                    continue

                # Crear una clave como "por publicar" o "por certificar", etc.
                clave_estado = " y ".join(sorted(set(pendientes)))

                if clave_estado not in estado_combinaciones:
                    estado_combinaciones[clave_estado] = []
                estado_combinaciones[clave_estado].append(titulo)

            # Construir la tabla HTML para cada dominio
            tabla_filas = ""
            for estado, reportes in estado_combinaciones.items():
                lista_reportes = [f"{i + 1}. {rep}" for i, rep in enumerate(reportes)]
                tabla_filas += f"""
                    <tr>
                        <th style='text-align:left;padding:8px;border:1px solid #ddd;width:30%;background-color:#ffffff;font-weight:bold;color:#1d8649;'>
                            {estado.capitalize()}
                        </th>
                        <td style='text-align:left;padding:8px;border:1px solid #ddd;width:70%;background-color:#ffffff;'>
                            {"<br>".join(lista_reportes)}
                        </td>
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



# ===============================================
# Rutas
# ===============================================
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Página principal con formulario de carga de archivo.
    Muestra mensajes almacenados en sesión, si los hay.
    """
    messages = request.session.pop("messages", [])
    return templates.TemplateResponse("index.html", {"request": request, "messages": messages})


@app.get("/status")
async def get_status():
    """Devuelve un estado simple en formato JSON."""
    return {"status": "active", "message": "Sistema funcionando correctamente"}


@app.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """
    Procesa la carga de archivo Excel y lee la hoja 'Estado Reportes'.
    Guarda el DataFrame en la variable global 'current_df' para uso posterior.
    """
    global current_df

    if not file.filename.endswith(".xlsx"):
        request.session["messages"] = ["Por favor sube un archivo Excel (.xlsx)"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    try:
        content = await file.read()
        excel_file = pd.ExcelFile(content)

        if "Estado Reportes" not in excel_file.sheet_names:
            request.session["messages"] = [
                "El archivo Excel no contiene la hoja 'Estado Reportes'"
            ]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        current_df = pd.read_excel(content, sheet_name="Estado Reportes")
        return RedirectResponse(url="/preview", status_code=status.HTTP_302_FOUND)

    except Exception as e:
        request.session["messages"] = [f"Error al procesar el archivo: {str(e)}"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.get("/preview", response_class=HTMLResponse)
async def preview(request: Request):
    """
    Muestra una tabla con todos los datos del DataFrame para verificar
    que los datos se hayan cargado correctamente.
    """
    global current_df

    if current_df is None:
        request.session["messages"] = ["No hay archivo cargado para previsualizar"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    try:
        reports = []
        for _, row in current_df.iterrows():
            report_data = {}
            # Mapeo dinámico de todas las columnas
            for column in current_df.columns:
                report_data[column.lower().replace(" ", "_")] = row.get(column, "")
            reports.append(report_data)

        summary = {
            "total_pending": len(reports),
            "columns": current_df.columns.tolist(),
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
    """
    Muestra la página para que el usuario seleccione la cuenta
    desde la cual se enviarán los correos.
    """
    accounts = [
        {"id": id_, "name": f"{config[0]} ({config[2]})"}
        for id_, config in SMTP_CONFIG.items()
    ]
    return templates.TemplateResponse(
        "select_account.html", {"request": request, "accounts": accounts}
    )


@app.post("/process_account")
async def process_account(request: Request):
    """
    Procesa la selección de la cuenta SMTP y la guarda en sesión.
    Redirige a la pantalla de confirmación de envío.
    """
    form_data = await request.form()
    account_id = form_data.get("account")

    if not account_id or account_id not in SMTP_CONFIG:
        request.session["messages"] = ["Por favor seleccione una cuenta válida"]
        return RedirectResponse(url="/select_account", status_code=status.HTTP_302_FOUND)

    email, password, account_type = SMTP_CONFIG[account_id]
    request.session["smtp_config"] = {
        "email": email,
        "password": password,
        "account_type": account_type,
    }

    return RedirectResponse(url="/confirm_send", status_code=status.HTTP_302_FOUND)


@app.get("/confirm_send", response_class=HTMLResponse)
async def confirm_send(request: Request):
    """
    Genera y muestra la vista previa de los correos que se enviarán
    agrupados por Data Owner y Dominio.
    """
    global current_df

    if current_df is None:
        request.session["messages"] = ["No hay datos para enviar"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    smtp_config = request.session.get("smtp_config")
    if not smtp_config:
        request.session["messages"] = ["Por favor seleccione una cuenta de envío"]
        return RedirectResponse(url="/select_account", status_code=status.HTTP_302_FOUND)

    try:
        preview_data = process_email_data(current_df)
        preview_emails = {}

        for data_owner, dominios in preview_data.items():
            email_content = []
            for dominio, data in dominios.items():
                email_html = templates.get_template("email_template.html").render(
                    request=request,
                    dominio=dominio,
                    email=data_owner,
                    tabla_filas=data["tabla_filas"],
                    mensaje_total=f"Total de reportes: {data['total_reportes']}"
                )
                email_content.append(email_html)

            # Combinar todo el HTML para este data_owner
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
        print(traceback.format_exc())
        request.session["messages"] = [f"Error al generar la vista previa: {str(e)}"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.post("/send_emails")
async def send_emails(request: Request):
    """
    Envía correos a cada Data Owner (según el contenido procesado).
    Se hace una sola conexión SMTP para optimizar.
    Muestra el resultado en un template final.
    """
    global current_df

    if current_df is None:
        request.session["messages"] = ["No hay datos para enviar"]
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    smtp_config = request.session.get("smtp_config")
    if not smtp_config:
        request.session["messages"] = ["Configuración de correo no encontrada"]
        return RedirectResponse(url="/select_account", status_code=status.HTTP_302_FOUND)

    try:
        preview_data = process_email_data(current_df)
        sent_emails = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Conexión SMTP única
        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.ehlo()
            server.starttls()
            server.ehlo()

            email = smtp_config["email"].strip()
            password = smtp_config["password"].strip()
            print(f"Intentando conexión con: {email}")
            server.login(email, password)
            print(f"Conexión SMTP exitosa con {email}")

        except smtplib.SMTPAuthenticationError as auth_error:
            print(f"Error de autenticación SMTP: {auth_error}")
            request.session["messages"] = [
                "Error de autenticación. Verifique las credenciales."
            ]
            return RedirectResponse(url="/confirm_send", status_code=status.HTTP_302_FOUND)
        except Exception as smtp_error:
            print(f"Error de conexión SMTP: {smtp_error}")
            request.session["messages"] = [
                "Error al conectar con el servidor de correo."
            ]
            return RedirectResponse(url="/confirm_send", status_code=status.HTTP_302_FOUND)

        # 2. Enviar correos a cada data_owner
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

                msg = MIMEMultipart()
                msg["From"] = email
                msg["To"] = data_owner
                msg["Subject"] = f"Estado de Reportes - {', '.join(domains_list)}"

                # Unir el contenido HTML
                full_html = "<hr/>".join(email_parts)
                msg.attach(MIMEText(full_html, "html", "utf-8"))

                server.send_message(msg)
                print(f"Correo enviado a {data_owner}")

                sent_emails.append({
                    "recipient": data_owner,
                    "timestamp": timestamp,
                    "total_reports": total_reports,
                    "domains": domains_list,
                })

            except Exception as e:
                print(f"Error enviando correo a {data_owner}: {str(e)}")
                continue

        # 3. Cerrar conexión SMTP
        server.quit()
        print("Conexión SMTP cerrada correctamente")

        if not sent_emails:
            raise Exception("No se pudo enviar ningún correo")

        # 4. Crear historial
        history = {
            "sender_email": email,
            "account_type": smtp_config["account_type"],
            "timestamp": timestamp,
            "total_recipients": len(sent_emails),
            "total_reports": sum(item["total_reports"] for item in sent_emails),
            "total_domains": len(set(dom for item in sent_emails for dom in item["domains"])),
            "sent_emails": sent_emails,
        }

        return templates.TemplateResponse("send_success.html", {"request": request, "history": history})

    except Exception as e:
        print(f"Error enviando correos: {str(e)}")
        print(traceback.format_exc())
        request.session["messages"] = [f"Error al enviar los correos: {str(e)}"]
        return RedirectResponse(url="/confirm_send", status_code=status.HTTP_302_FOUND)


# ===============================================
# Punto de entrada
# ===============================================
if __name__ == "__main__":
    uvicorn.run("estado:app", host="0.0.0.0", port=8000, reload=True)
