import logging
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
import subprocess
import os
import time
import sys
import socket
import psutil
import signal
from fastapi.templating import Jinja2Templates
from fastapi import Request
from fastapi.staticfiles import StaticFiles

# Obtener el directorio base del proyecto
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Inicializar FastAPI
app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurar templates
templates = Jinja2Templates(directory="templates")

# Definir los proyectos disponibles usando rutas relativas
PROJECTS = {
    "estado": {
        "name": "Estado de Reportes",
        "module": "estado.estado:app",  # Cambiado para reflejar la estructura correcta
        "port": 8001,
        "color": "#007bff",
        "path": BASE_DIR,  # Cambiado para usar el directorio base
        "factory": False,  # Agregar esta línea
    },
    "sellos": {
        "name": "Sellos de Reportes",
        "module": "Sellos.sellos:create_app",  # Fixed module path
        "port": 8002,
        "color": "#28a745",
        "factory": True,
        "path": os.path.join(BASE_DIR, "Sellos"),
    },
    "usabilidad": {
    "name": "Usabilidad de Reportes",
    "module": "usabilidad:create_app", 
    "port": 8003,
    "color": "#ff0000",
    "factory": True,
    "path": os.path.join(BASE_DIR, "usabilidad"),
    },
}


def puerto_en_uso(puerto):
    """Verifica si un puerto está en uso"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", puerto)) == 0


def matar_proceso_en_puerto(puerto):
    """Mata el proceso que está usando un puerto específico"""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            # Obtener todas las conexiones del proceso
            conexiones = proc.connections("tcp")
            for conn in conexiones:
                if conn.laddr.port == puerto:
                    os.kill(proc.info["pid"], signal.SIGTERM)
                    time.sleep(1)  # Esperar a que el proceso termine
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False


def verificar_directorio(path):
    """Verifica si un directorio existe y es accesible"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"El directorio no existe: {path}")
    if not os.path.isdir(path):
        raise NotADirectoryError(f"La ruta no es un directorio: {path}")
    if not os.access(path, os.R_OK | os.X_OK):
        raise PermissionError(f"No hay permisos suficientes para acceder a: {path}")


def iniciar_proyecto(proyecto_id):
    """Ejecuta un proyecto en segundo plano con uvicorn desde su directorio"""
    try:
        if proyecto_id not in PROJECTS:
            logger.error(f"Proyecto no encontrado: {proyecto_id}")
            return HTMLResponse(
                content="<h1>Proyecto no encontrado</h1>", status_code=404
            )

        proyecto = PROJECTS[proyecto_id]
        puerto = proyecto["port"]

        logger.info(f"Iniciando proyecto {proyecto_id} en puerto {puerto}")

        # Verificar que el directorio del proyecto existe
        try:
            verificar_directorio(proyecto["path"])
        except Exception as e:
            return HTMLResponse(
                content=f"""
                <h1>Error al acceder al directorio del proyecto</h1>
                <p>{str(e)}</p>
                <p>Ruta: {proyecto["path"]}</p>
                """,
                status_code=500,
            )

        # Verificar si el puerto está en uso y matar el proceso si es necesario
        if puerto_en_uso(puerto):
            if not matar_proceso_en_puerto(puerto):
                return HTMLResponse(
                    content=f"<h1>Error: Puerto {puerto} en uso y no se pudo liberar</h1>",
                    status_code=500,
                )

        # Configurar el entorno para el subproceso con mejor manejo del PYTHONPATH
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [BASE_DIR, env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)

        logger.info(f"PYTHONPATH configurado como: {env['PYTHONPATH']}")
        logger.info(f"Archivos en directorio: {os.listdir(proyecto['path'])}")

        comando = [
            sys.executable,
            "-m",
            "uvicorn",
            proyecto["module"],
            "--host",
            "127.0.0.1",
            "--port",
            str(puerto),
        ]

        logger.info(f"Ejecutando comando: {' '.join(comando)}")
        logger.info(f"En directorio: {proyecto['path']}")

        try:
            proceso = subprocess.Popen(
                comando,
                cwd=proyecto["path"],
                env=env,
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )

            # Esperar un poco y verificar si el proceso sigue vivo
            time.sleep(2)
            if proceso.poll() is not None:
                error = proceso.stderr.read()
                logger.error(f"Error al iniciar el proyecto: {error}")
                return HTMLResponse(
                    content=f"<h1>Error al iniciar el proyecto:</h1><pre>{error}</pre>",
                    status_code=500,
                )

            return RedirectResponse(url=f"http://127.0.0.1:{puerto}", status_code=302)

        except subprocess.SubprocessError as e:
            logger.error(f"Error en subprocess: {str(e)}")
            return HTMLResponse(
                content=f"<h1>Error al ejecutar el proyecto: {str(e)}</h1>",
                status_code=500,
            )

    except Exception as e:
        logger.exception("Error inesperado al iniciar el proyecto")
        return HTMLResponse(
            content=f"""
            <h1>Error inesperado</h1>
            <p>{str(e)}</p>
            <p>Proyecto: {proyecto_id}</p>
            <p>Ruta: {PROJECTS[proyecto_id]["path"]}</p>
            """,
            status_code=500,
        )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Página principal con botones dinámicos para iniciar proyectos"""
    return templates.TemplateResponse(
        "menu.html", {"request": request, "projects": PROJECTS}
    )


@app.get("/iniciar/{proyecto_id}/", response_class=HTMLResponse)
async def iniciar(proyecto_id: str):
    """Inicia el proyecto seleccionado"""
    return iniciar_proyecto(proyecto_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_menu:app", host="0.0.0.0", port=8000, reload=True)
