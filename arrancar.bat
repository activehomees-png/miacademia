@echo off
echo Instalando dependencias...
py -m pip install -r requirements.txt --quiet
echo.
echo Iniciando academia en http://127.0.0.1:5002
echo Admin: admin@academia.com / admin123
echo.
py app.py
pause
