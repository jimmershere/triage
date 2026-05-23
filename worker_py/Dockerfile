FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY translators ./translators
COPY pyx12 ./pyx12
COPY tools_x12_cms_harness.py .
COPY file_profiler.py .
COPY complexity_scorer.py .
COPY routing_engine.py .
COPY tier_executor.py .
COPY tier1_assist.py .
COPY tier2_swarm.py .
COPY worker.py .
ENV PYTHONUNBUFFERED=1
CMD ["python", "worker.py"]
