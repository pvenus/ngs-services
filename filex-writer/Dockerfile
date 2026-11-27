FROM python:3.12-slim
WORKDIR /app
COPY server.py scope_registry.py scopes.json single_target_delete.py portrait_assets.py media_store.py media_validation.py npc_store.py npc_assets.py concept_store.py concept_lifecycle.py concept_markdown.py motion_store.py fenced_store.py owned_store.py ownership_plan.py npc_document.py npc_planning_markdown.py migration_store.py frame_maintenance.py native_pixel_direction.py workflow_native_media.py workflow_audio_store.py prop_package_contract.py prop_package_store.py map_store.py assembly_store.py assembly_contract.py /app/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python", "-B", "/app/server.py"]
