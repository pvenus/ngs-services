FROM python:3.12-slim
WORKDIR /app
COPY server.py portrait_assets.py media_store.py media_validation.py npc_store.py npc_assets.py concept_store.py concept_lifecycle.py motion_store.py fenced_store.py owned_store.py ownership_plan.py npc_document.py migration_store.py frame_maintenance.py /app/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WRITER_BIND=0.0.0.0 WRITER_ROOT=/canonical WRITER_TOKEN_FILE=/run/secrets/writer_token
USER 1000:1000
CMD ["python", "-B", "/app/server.py"]
