@echo off

echo Iniciando APP ENVIO CORREO...
python -m uvicorn web_menu:app --reload --host 127.0.0.1 --port 8000

echo Presiona una tecla para cerrar...
pause
