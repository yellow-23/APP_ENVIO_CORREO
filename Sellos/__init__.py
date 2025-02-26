# This file makes the directory a Python package
from .sellos import create_app

# This allows uvicorn to import the application factory
__all__ = ['create_app']
