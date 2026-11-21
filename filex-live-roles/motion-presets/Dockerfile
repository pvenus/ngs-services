FROM python:3.12-slim
WORKDIR /app
COPY server.py npc_store.py npc_assets.py concept_store.py motion_store.py /app/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WRITER_BIND=0.0.0.0 WRITER_ROOT=/canonical WRITER_TOKEN_FILE=/run/secrets/writer_token
USER 1000:1000
CMD ["python", "-B", "/app/server.py"]
