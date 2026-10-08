"""
Módulo de Configuração Centralizada e Suporte a Multi-Tenant via .env.
"""
import os
import secrets
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

# Leitor nativo de fallback para .env: preserva variáveis já injetadas no ambiente (ex: Docker)
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                if k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")

from datetime import timedelta

class Config:
    # Identificação do Tenant
    TENANT_ID = os.getenv("TENANT_ID", "default_tenant")
    TENANT_NAME = os.getenv("TENANT_NAME", "Cliente Padrao")
    MAX_LICENSES = int(os.getenv("MAX_LICENSES", "5"))

    # Segurança Flask & Cookies de Sessão
    # Sem uma chave configurada, cria uma chave efêmera para evitar usar um segredo
    # público. Em produção, SECRET_KEY deve ser definido no ambiente.
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)
    SESSION_COOKIE_NAME = f"orderflow_session_{os.getenv('TENANT_ID', 'default')}"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # Configuração da API Sankhya OM (Credenciais mantidas no servidor backend / .env)
    SANKHYA_BASE_URL = os.getenv("SANKHYA_BASE_URL", "")
    SANKHYA_APPKEY = os.getenv("SANKHYA_APPKEY", "")
    SANKHYA_USERNAME = os.getenv("SANKHYA_USERNAME", "")
    SANKHYA_PASSWORD = os.getenv("SANKHYA_PASSWORD", "")

    # Painel Admin Central (Agrupador)
    ADMIN_PANEL_URL = os.getenv("ADMIN_PANEL_URL", "http://127.0.0.1:5005")
    ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")

    # Timeouts e Re-tentativas HTTP
    HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "25"))
