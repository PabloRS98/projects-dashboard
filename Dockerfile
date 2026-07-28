FROM python:3.12-slim

# git es imprescindible en tiempo de ejecución: el escaneo local lo invoca por
# subprocess para leer rama, último commit y cambios sin commitear.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Marca las rutas montadas como seguras: los repos del host pertenecen a otro
# UID y, sin esto, git aborta con "detected dubious ownership".
RUN git config --system --add safe.directory '*'

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# /data → base de datos y backups (lectura-escritura)
# /repos → repositorios locales a vigilar (basta lectura)
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/salud')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
