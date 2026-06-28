FROM python:3.12-slim
WORKDIR /app
# Build context is the repo root (see docker-compose.yml api.build.context) so the
# image can bundle the repo-root packages the api imports instead of relying on
# runtime bind-mounts. Install both the api deps and the worker_py runtime deps so
# the bundled worker_py modules (turbo_pipeline, validation, reporting, fhir,
# mapping_advisor, ...) import cleanly.
COPY api/requirements.txt /tmp/api-requirements.txt
COPY worker_py/requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir -r /tmp/api-requirements.txt -r /tmp/worker-requirements.txt
# Bundle the api package plus the repo-root packages it imports so the image is
# self-sufficient. The layout mirrors the runtime override bind-mounts:
#   /app/api  /app/claimtrace  /app/worker_py
# api.* and claimtrace.* resolve via PYTHONPATH=/app; worker_py modules
# (turbo_pipeline, fhir, ...) resolve via the "../worker_py" sys.path insert that
# turbo_routes/mapping_routes/partners_routes perform relative to /app/api.
COPY api /app/api
COPY claimtrace /app/claimtrace
COPY worker_py /app/worker_py
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
