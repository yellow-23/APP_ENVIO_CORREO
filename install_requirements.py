"""
Script to install the required packages for the application.
Run this with: python install_requirements.py
"""

import subprocess
import sys

def install_packages():
    required_packages = [
        'fastapi',
        'uvicorn',
        'pandas',
        'openpyxl',
        'jinja2',
        'python-multipart',
        'starlette',
        'werkzeug',
        'itsdangerous',
        'psutil',
        'aiofiles',

    ]
    
    print("Installing required packages...")
    for package in required_packages:
        print(f"Installing {package}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    
    print("\nAll required packages installed successfully!")
    print("You can now run the application with: python -m uvicorn sellos:app --reload")

if __name__ == "__main__":
    install_packages()
