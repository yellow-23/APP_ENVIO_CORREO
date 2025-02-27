
echo "Iniciando APP ENVÍO CORREO..."
python3 -m uvicorn web_menu:app --reload --host 127.0.0.1 --port 8080

echo "Presiona Enter para cerrar..."
read -r  # `pause` no existe en Bash, `read` espera la entrada del usuario