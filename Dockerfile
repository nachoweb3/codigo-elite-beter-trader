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
# Hugging Face Spaces usa 7860; Render inyecta su propio PORT.
ENV PORT=7860
EXPOSE 7860

# Un worker es intencional: las sesiones de wallet son en memoria.
# Render sobrescribe PORT; localmente se puede usar 8000 explícitamente.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
