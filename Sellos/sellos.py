from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from werkzeug.utils import secure_filename

# Configuración simplificada
class Settings:
    SECRET_KEY: str = "tu_clave_secreta_aqui"
    UPLOAD_FOLDER: str = "uploads"
    TEMPLATES_DIR: str = "templates"
    STATIC_DIR: str = "static"

settings = Settings()

# Asegurar directorios necesarios
os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(settings.STATIC_DIR, exist_ok=True)

# Sistema de mensajes flash
class Flash:
    def __init__(self):
        self.messages = []

    def get_messages(self):
        messages = self.messages.copy()
        self.messages.clear()
        return messages

    def add_message(self, message, category='info'):
        self.messages.append((category, message))

flash = Flash()

def get_flashed_messages(with_categories=False):
    messages = flash.get_messages()
    if with_categories:
        return messages
    return [message for _, message in messages]

# Configuración SMTP
SMTP_CONFIG = {
    "1": ("cflores.practica@cmpc.com", "ywsb sfgz fmyf qdsg", "personal"),
    "2": ("datadriven@cmpc.cl", "ccgu zixq lzme xmsr", "DataDriven")
}

def create_app():
    """Factory function to create and configure the FastAPI application"""
    app = FastAPI()
    
    # Configure middleware
    app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)
    
    # Configure templates
    templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)
    templates.env.globals['get_flashed_messages'] = get_flashed_messages
    
    # Mount static files
    app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

    # Helper functions
    def clean_title(title):
        """Limpia y formatea el título del reporte eliminando texto adicional después del guión"""
        if not pd.notna(title):
            return ""
        title = str(title)
        # Remove text after hyphen and any .docx extension
        workspace_start = title.find(" - [")
        if (workspace_start != -1):
            title = title[:workspace_start]
        return title.strip().replace('.docx', '')

    def formatear_sellos(row):
        """Convierte los sellos booleanos a texto legible (Tecnología, Negocio, Seguridad)"""
        sellos = []
        for sello, nombre in [('SelloTécnico', 'Tecnología'), 
                            ('SelloNegocio', 'Negocio'), 
                            ('SelloSeguridad', 'Seguridad')]:
            if row.get(sello, False):
                sellos.append(nombre)
        return ' ; '.join(sellos) if sellos else 'Sin sellos'

    def format_empty_value(value):
        """Reemplaza valores vacíos o NaN por 'Sin información' para mejor presentación"""
        if pd.isna(value) or value == '' or str(value).lower() == 'nan':
            return "Sin información"
        return str(value)

    def crear_contenido_html(reportes_por_dominio, owner_email):
        """Genera el contenido HTML del correo con las tablas de reportes por dominio"""
        try:
            contenido_dominios = []
            dominios_ordenados = sorted(str(key) for key in reportes_por_dominio.keys() 
                                    if pd.notna(key) and key is not None)
            
            for dominio in dominios_ordenados:
                if not dominio or not reportes_por_dominio[dominio]:
                    continue
                    
                reportes = reportes_por_dominio[dominio]
                rows = []
                
                for reporte in reportes:
                    if isinstance(reporte, pd.Series):
                        reporte = reporte.to_dict()
                    
                    rows.append(f"""
                    <tr>
                        <td style="width: 25%; padding: 8px;">{format_empty_value(reporte.get('WorkSpace', ''))}</td>
                        <td style="width: 35%; padding: 8px;">{format_empty_value(clean_title(reporte.get('Titulo', '')))}</td>
                        <td style="width: 20%; padding: 8px;">{format_empty_value(reporte.get('Responsable', ''))}</td>
                        <td style="width: 20%; padding: 8px;">{formatear_sellos(reporte)}</td>
                    </tr>
                    """)
                
                if rows:
                    contenido_dominios.append(f"""
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
                    """)
            
            if not contenido_dominios:
                return None
                
            template_path = os.path.join(os.path.dirname(__file__), 'email_template.html')
            with open(template_path, 'r', encoding='utf-8') as file:
                template = file.read()
            
            return template.replace('{contenido_reportes}', ''.join(contenido_dominios))\
                        .replace('{owner_email}', str(owner_email))
            
        except Exception as e:
            print(f"Error en crear_contenido_html: {str(e)}")
            return None

    def check_email_sent(owner_email, df):
        """Verifica si ya se envió el correo al Data Owner basado en el estado del Excel"""
        owner_reports = df[df['DataOwner_Lgobierno'] == owner_email]
        if owner_reports.empty:
            return False
            
        # Verificar si hay al menos un reporte sin estado de envío
        estados = owner_reports['Estado Solicitudes'].fillna('')
        fechas = owner_reports['Fecha envío'].fillna('')
        
        # Si hay algún reporte sin estado o fecha de envío, consideramos que no se ha enviado
        pendientes = any(
            (estado.lower().strip() != 'correo enviado' or not fecha.strip())
            for estado, fecha in zip(estados, fechas)
        )
        
        return not pendientes

    async def process_excel_file(filepath, email_option=None):
        """Version asíncrona de process_excel_file"""
        try:
            if not filepath or not os.path.exists(filepath):
                return "Error: Archivo no encontrado", []
                
            df = pd.read_excel(filepath, engine='openpyxl')
            
            # Validación básica de columnas
            expected_columns = [
                'Dominio', 'WorkSpace', 'SelloTécnico', 'SelloNegocio',
                'SelloSeguridad', 'Titulo', 'DataOwner_Lgobierno', 
                'DataStewards', 'Responsable'
            ]
            
            missing_columns = [col for col in expected_columns if col not in df.columns]
            if missing_columns:
                return "Error: Formato de Excel inválido", []

            df_filtered = df[~df['SelloNegocio']]
            sender_email, app_password, _ = SMTP_CONFIG[email_option]
            history_items = []

            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(sender_email, app_password)

                # Agrupar por Data Owner
                grouped = df_filtered.groupby('DataOwner_Lgobierno')
                for owner, owner_df in grouped:
                    if not pd.notna(owner) or '@' not in str(owner):
                        continue

                    # Agrupar reportes por dominio
                    reportes_por_dominio = {}
                    for _, row in owner_df.iterrows():
                        dominio = str(row['Dominio'])
                        if dominio not in reportes_por_dominio:
                            reportes_por_dominio[dominio] = []
                        reportes_por_dominio[dominio].append(row)

                    # Crear y enviar correo
                    html_content = crear_contenido_html(reportes_por_dominio, owner)
                    if not html_content:
                        continue

                    msg = MIMEMultipart('alternative')
                    msg['From'] = sender_email
                    msg['To'] = owner
                    msg['Subject'] = "Reportes pendientes de asignación de sello de Negocio"
                    msg.attach(MIMEText(html_content, 'html'))

                    # Manejar CC
                    cc_list = []
                    stewards = owner_df['DataStewards'].dropna().unique()
                    for steward in stewards:
                        cc_list.extend([email.strip() for email in str(steward).split(',') if '@' in email.strip()])
                    
                    if cc_list:
                        msg['Cc'] = ', '.join(set(cc_list))  # Eliminar duplicados

                    recipients = [owner] + cc_list
                    server.send_message(msg, to_addrs=recipients)

                    # Actualizar estado en el DataFrame original
                    df.loc[df['DataOwner_Lgobierno'] == owner, 'Estado Solicitudes'] = 'Correo enviado'
                    df.loc[df['DataOwner_Lgobierno'] == owner, 'Fecha envío'] = pd.Timestamp.now().strftime('%Y-%m-%d')

                    # Registrar en historial
                    history_items.append({
                        'owner': owner,
                        'domain': sorted(reportes_por_dominio.keys()),
                        'timestamp': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')
                    })

            # Guardar cambios
            df.to_excel(filepath, index=False)
            return "Proceso completado exitosamente", history_items

        except Exception as e:
            return f"Error: {str(e)}", []

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "messages": get_flashed_messages(with_categories=True)}
        )

    @app.post("/upload")
    async def upload_file(request: Request, file: UploadFile = File(...)):
        try:
            # Verificar extensión
            if not file.filename.endswith('.xlsx'):
                flash.add_message('Formato de archivo no válido', 'error')
                return RedirectResponse(url="/", status_code=303)

            # Crear directorio si no existe
            os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
            
            # Guardar archivo
            filename = secure_filename(file.filename)
            filepath = os.path.join(settings.UPLOAD_FOLDER, filename)
            
            contents = await file.read()
            with open(filepath, 'wb') as f:
                f.write(contents)

            # Procesar Excel
            df = pd.read_excel(filepath)
            
            # Guardar en sesión
            request.session["current_file"] = filepath
            request.session["current_step"] = "review"
            request.session["summary"] = {
                'total_reports': len(df),
                'pending_review': len(df[~df['SelloNegocio']]),
                'with_business_seal': len(df[df['SelloNegocio']]),
                'domains': df['Dominio'].nunique(),
                'data_owners': df['DataOwner_Lgobierno'].dropna().nunique()
            }

            return RedirectResponse(url="/review", status_code=303)
                
        except Exception as e:
            flash.add_message(f'Error al procesar el archivo: {str(e)}', 'error')
            return RedirectResponse(url="/", status_code=303)

    @app.get("/review")
    async def review_data(request: Request):
        if "current_file" not in request.session:
            flash.add_message('No hay archivo para procesar', 'error')
            return RedirectResponse(url="/", status_code=303)
        
        try:
            df = pd.read_excel(request.session["current_file"])
            # Ordenar el DataFrame por Dominio y Título
            df_pending = df[~df['SelloNegocio']].sort_values(['Dominio', 'Titulo'])
            
            reports = []
            for _, row in df_pending.iterrows():
                sellos = []
                if row['SelloTécnico']: sellos.append('Tecnología')
                if row['SelloSeguridad']: sellos.append('Seguridad')
                
                reports.append({
                    'dominio': format_empty_value(row['Dominio']),
                    'titulo': clean_title(row['Titulo']),
                    'workspace': format_empty_value(row['WorkSpace']),
                    'responsable': format_empty_value(row['Responsable']),
                    'data_owner': format_empty_value(row['DataOwner_Lgobierno']),  # Aseguramos que esté incluido
                    'sellos': ' ; '.join(sellos) if sellos else 'Sin sellos'
                })
            
            summary = {
                'filename': os.path.basename(request.session["current_file"]),
                'total_pending': len(df_pending),
                'reports': reports
            }
            
            request.session['summary'] = summary
            return templates.TemplateResponse(
                "review.html",
                {
                    "request": request,
                    "summary": summary
                }
            )
            
        except Exception as e:
            flash.add_message(f'Error al procesar archivo: {str(e)}', 'error')
            return RedirectResponse(url="/", status_code=303)

    @app.get("/confirm_send")
    @app.post("/confirm_send")
    async def confirm_send(request: Request):
        """Maneja tanto GET como POST para confirm_send"""
        if "current_file" not in request.session:
            flash.add_message('No hay archivo para procesar', 'error')
            return RedirectResponse(url="/", status_code=303)
            
        try:
            df = pd.read_excel(request.session["current_file"])
            request.session["current_step"] = "send"
            return templates.TemplateResponse("confirm_send.html", {
                "request": request,
                "current_step": "send"
            })
        except Exception as e:
            flash.add_message(f'Error al procesar archivo: {str(e)}', 'error')
            return RedirectResponse(url="/", status_code=303)

    @app.post("/process")
    async def process(request: Request, email_option: str = Form(...)):
        """Procesa la selección de cuenta de correo"""
        if "current_file" not in request.session:
            flash.add_message('No hay archivo para procesar', 'error')
            return RedirectResponse(url="/", status_code=303)
        
        try:
            # Guardar la opción en la sesión inmediatamente
            request.session["email_option"] = email_option
            
            if email_option not in SMTP_CONFIG:
                flash.add_message('Opción de correo inválida', 'error')
                return RedirectResponse(url="/confirm_send", status_code=303)
                
            # Redirigir a preview_emails
            return RedirectResponse(url="/preview_emails", status_code=303)
                
        except Exception as e:
            flash.add_message(f'Error en el procesamiento: {str(e)}', 'error')
            return RedirectResponse(url="/", status_code=303)

    @app.get("/preview_emails")
    async def preview_emails(request: Request):
        if "current_file" not in request.session:
            flash.add_message('No hay información para procesar', 'error')
            return RedirectResponse(url="/", status_code=303)
            
        try:
            email_option = request.session.get("email_option")
            if not email_option:
                flash.add_message('Debe seleccionar una cuenta de correo', 'error')
                return RedirectResponse(url="/confirm_send", status_code=303)
            
            df = pd.read_excel(request.session["current_file"])
            df_filtered = df[~df['SelloNegocio']]
            
            if df_filtered['DataOwner_Lgobierno'].dropna().nunique() == 0:
                flash.add_message('Error: No hay Data Owners válidos en el archivo.', 'error')
                return RedirectResponse(url="/", status_code=303)

            preview_data = {
                'sender_email': SMTP_CONFIG[email_option][0],
                'account_type': SMTP_CONFIG[email_option][2],
                'recipients': {}
            }
            
            for _, row in df_filtered.iterrows():
                owner = row['DataOwner_Lgobierno']
                if pd.notna(owner) and '@' in str(owner):
                    if owner not in preview_data['recipients']:
                        preview_data['recipients'][owner] = []
                        
                    preview_data['recipients'][owner].append({
                        'dominio': format_empty_value(row['Dominio']),
                        'titulo': clean_title(row['Titulo']),
                        'workspace': format_empty_value(row['WorkSpace']),
                        'responsable': format_empty_value(row['Responsable']),
                        'sellos': formatear_sellos(row)
                    })
                    
                    
                    
            print("DEBUG: request.session =", dict(request.session))
            

            return templates.TemplateResponse("preview_emails.html", {
                "request": request,
                "preview": preview_data
            })
            
        except Exception as e:
            flash.add_message(f'Error al generar vista previa: {str(e)}', 'error')
            return RedirectResponse(url="/confirm_send", status_code=303)

    @app.post("/send_emails")
    async def send_emails(request: Request):
        if "current_file" not in request.session:
            flash.add_message('No hay archivo para procesar', 'error')
            return RedirectResponse(url="/", status_code=303)
            
        try:
            email_option = request.session.get("email_option")
            if not email_option:
                flash.add_message('Error: No se seleccionó cuenta de correo', 'error')
                return RedirectResponse(url="/confirm_send", status_code=303)
            
            import time
            time.sleep(1)  # Simular tiempo de conexión
            
            df = pd.read_excel(request.session["current_file"])
            df_filtered = df[~df['SelloNegocio']]
            
            # Preparar datos para el historial
            history_data = {
                'sender_email': SMTP_CONFIG[email_option][0],
                'account_type': SMTP_CONFIG[email_option][2],
                'total_sent': len(df_filtered['DataOwner_Lgobierno'].unique()),
                'total_reports': len(df_filtered),
                'total_domains': df_filtered['Dominio'].nunique(),
                'sent_emails': []
            }
            
            result, history_items = await process_excel_file(request.session["current_file"], email_option)
            
            if 'Error' not in result:
                # Agregar información de envíos al historial
                for item in history_items:
                    history_data['sent_emails'].append({
                        'recipient': item['owner'],
                        'timestamp': item['timestamp'],
                        'reports': len(item['domain']),
                        'domain_list': item['domain'],
                        'cc_count': len(item['domain'])
                    })
                
                request.session['history_data'] = history_data
                return templates.TemplateResponse("history.html", {
                    "request": request, 
                    "history": history_data
                })
            
            flash.add_message(result, 'error')
            return RedirectResponse(url="/", status_code=303)
            
        except Exception as e:
            flash.add_message(f'Error en el envío: {str(e)}', 'error')
            return RedirectResponse(url="/", status_code=303)
        finally:
            await cleanup_session(request)

    async def cleanup_session(request: Request):
        """Limpia los archivos temporales y la sesión"""
        if 'current_file' in request.session:
            try:
                os.remove(request.session['current_file'])
            except:
                pass
            request.session.pop('current_file', None)
        request.session.pop('summary', None)
        request.session.pop('email_summary', None)
        request.session.pop('email_option', None)
        request.session.pop('current_step', None)

    return app

# Create the app instance directly
app = create_app()

if __name__ == "__main__":
    import uvicorn
    import os
    import sys
    
    # Add the parent directory to Python path if needed
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    uvicorn.run(
        "Sellos.sellos:app",  # Use full module path
        host="0.0.0.0",
        port=8000,
        reload=True
    )