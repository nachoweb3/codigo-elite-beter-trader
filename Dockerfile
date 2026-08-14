# CE BetterTrader PRO — imagen de producción
FROM python:3.11-slim

WORKDIR /app

# Dependencias primero para aprovechar la caché de Docker.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código de la aplicación.
COPY . .
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV PORT=8000
EXPOSE 8000

# Un worker es intencional: las sesiones de wallet son en memoria.
# Render inyecta PORT; localmente se usa 8000.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
