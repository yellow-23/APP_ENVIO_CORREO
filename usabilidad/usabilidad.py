import os
import pandas as pd
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from fastapi import FastAPI, Request, UploadFile, File, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

def create_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

    # 📂 Configuración de rutas
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
    
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    
    templates = Jinja2Templates(directory=TEMPLATE_DIR)

    # 🌐 Configuración SMTP
    SMTP_CONFIG = {
        "1": ("cflores.practica@cmpc.com", "ywsb sfgz fmyf qdsg", "personal"),
        "2": ("datadriven@cmpc.cl", "ccgu zixq lzme xmsr", "DataDriven")
    }

    # 📌 Variable global para almacenar el DataFrame temporalmente
    current_df = None

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Página de inicio con formulario de carga."""
        messages = request.session.pop("messages", [])
        return templates.TemplateResponse("index.html", {
            "request": request,
            "messages": messages
        })

    @app.get("/status")
    async def get_status():
        """Endpoint para verificar el estado del sistema."""
        return {"status": "active", "message": "Sistema funcionando correctamente"}

    @app.post("/upload")
    async def upload_file(request: Request, file: UploadFile = File(...)):
        """Procesa la subida del archivo Excel."""
        nonlocal current_df
        
        if not file.filename.endswith(".xlsx"):
            request.session["messages"] = ["Por favor sube un archivo Excel (.xlsx)"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        try:
            content = await file.read()
            excel_file = pd.ExcelFile(content)

            # 📌 Verificar si existe la hoja "Usabilidad"
            if "Usabilidad" not in excel_file.sheet_names:
                request.session["messages"] = ["El archivo Excel no contiene la hoja 'Usabilidad'"]
                return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
            
            current_df = pd.read_excel(content, sheet_name="Usabilidad")

            return RedirectResponse(url="/preview", status_code=status.HTTP_302_FOUND)

        except Exception as e:
            request.session["messages"] = [f"Error al procesar el archivo: {str(e)}"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    @app.get("/preview", response_class=HTMLResponse)
    async def preview(request: Request):
        """Muestra una vista previa del archivo Excel procesado."""
        nonlocal current_df

        if current_df is None:
            request.session["messages"] = ["No hay archivo cargado para previsualizar"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        try:
            reports = []
            for _, row in current_df.iterrows():
                report_data = {col.lower().replace(' ', '_'): row.get(col, '') for col in current_df.columns}
                reports.append(report_data)

            summary = {
                'total_pending': len(reports),
                'columns': current_df.columns.tolist(),
                'reports': reports
            }

            return templates.TemplateResponse("preview.html", {
                "request": request,
                "summary": summary
            })
        except Exception as e:
            request.session["messages"] = [f"Error al generar la vista previa: {str(e)}"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    @app.get("/select_account", response_class=HTMLResponse)
    async def select_account(request: Request):
        """Muestra la página de selección de cuenta SMTP."""
        accounts = [{"id": id, "name": f"{config[0]} ({config[2]})"} for id, config in SMTP_CONFIG.items()]
        
        return templates.TemplateResponse("select_account.html", {
            "request": request,
            "accounts": accounts
        })

    @app.post("/process_account")
    async def process_account(request: Request):
        """Procesa la selección de cuenta de correo."""
        form_data = await request.form()
        account_id = form_data.get("account")

        if not account_id or account_id not in SMTP_CONFIG:
            request.session["messages"] = ["Por favor seleccione una cuenta válida"]
            return RedirectResponse(url="/select_account", status_code=status.HTTP_302_FOUND)

        email, password, account_type = SMTP_CONFIG[account_id]
        request.session["smtp_config"] = {
            "email": email,
            "password": password,
            "account_type": account_type
        }

        return RedirectResponse(url="/confirm_send", status_code=status.HTTP_302_FOUND)

    @app.get("/confirm_send", response_class=HTMLResponse)
    async def confirm_send(request: Request):
        """Muestra la vista previa del correo antes de enviarlo."""
        nonlocal current_df

        if current_df is None:
            request.session["messages"] = ["No hay datos para enviar"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        smtp_config = request.session.get("smtp_config")
        if not smtp_config:
            request.session["messages"] = ["Por favor seleccione una cuenta de envío"]
            return RedirectResponse(url="/select_account", status_code=status.HTTP_302_FOUND)

        preview_data = current_df.to_dict(orient="records")

        return templates.TemplateResponse("confirm_send.html", {
            "request": request,
            "smtp_config": smtp_config,
            "preview_data": preview_data
        })

    @app.post("/send_emails")
    async def send_emails(request: Request):
        """Envía los correos electrónicos y muestra el resumen."""
        nonlocal current_df

        if current_df is None:
            request.session["messages"] = ["No hay datos para enviar"]
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

        smtp_config = request.session.get("smtp_config")
        if not smtp_config:
            request.session["messages"] = ["Configuración de correo no encontrada"]
            return RedirectResponse(url="/select_account", status_code=status.HTTP_302_FOUND)

        sent_emails = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(smtp_config["email"], smtp_config["password"])

            for _, row in current_df.iterrows():
                recipient = row.get("email")
                if not recipient:
                    continue

                msg = MIMEMultipart()
                msg["From"] = smtp_config["email"]
                msg["To"] = recipient
                msg["Subject"] = "Notificación de Usabilidad"

                email_content = f"""
                <html>
                <body>
                    <h3>Reporte de Usabilidad</h3>
                    <p>{row.to_dict()}</p>
                </body>
                </html>
                """
                msg.attach(MIMEText(email_content, "html"))
                server.send_message(msg)
                sent_emails.append(recipient)

            server.quit()

            return templates.TemplateResponse("send_success.html", {
                "request": request,
                "history": {"timestamp": timestamp, "sent_emails": sent_emails}
            })

        except Exception as e:
            request.session["messages"] = [f"Error al enviar correos: {str(e)}"]
            return RedirectResponse(url="/confirm_send", status_code=status.HTTP_302_FOUND)

    return app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("usabilidad:create_app", host="0.0.0.0", port=8000, reload=True, factory=True)
