# Imagem oficial Python enxuta e estável
FROM python:3.11-slim

# Evita que o Python gere arquivos .pyc e força logs em tempo real sem buffer
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instala dependências do sistema necessárias para compilação mínima
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copia requisitos e instala dependências Python em camada otimizada de cache
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia o código-fonte da aplicação
COPY . .

# Porta padrão do OrderFlow
EXPOSE 6000

# Execução com servidor WSGI Gunicorn (4 workers para atender requisições concorrentes)
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:6000", "--timeout", "120", "app:app"]
