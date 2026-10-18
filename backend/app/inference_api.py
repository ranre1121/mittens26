from dataclasses import replace
from pathlib import Path
import os

from app.api import create_app
from app.config import load_settings


def create_inference_app():
    key = os.environ.get('INFERENCE_API_KEY', '')
    if not key:
        raise ValueError('INFERENCE_API_KEY is required for the inference service')
    settings = replace(load_settings(), api_keys=(key,), frontend_dir=Path('/nonexistent-inference-frontend'))
    app = create_app(settings)
    app.title = 'Datathon NVIDIA Inference API'
    return app
