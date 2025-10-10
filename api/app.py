import os, base64, json, uuid, logging
import pika
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("api")

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")

app = FastAPI(title="TurboEDI Ingest API", version="0.1.0")

def get_channel():
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    ch = connection.channel()
    ch.queue_declare(queue="ingest", durable=True)
    return connection, ch

@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty file")
        job_id = str(uuid.uuid4())
        payload = {
            "job_id": job_id,
            "filename": file.filename or "upload.dat",
            "size": len(content),
            "data_b64": base64.b64encode(content).decode("ascii"),
        }
        connection, ch = get_channel()
        ch.basic_publish(
            exchange="",
            routing_key="ingest",
            body=json.dumps(payload).encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2  # persistent
            ),
        )
        connection.close()
        logger.info("Enqueued job %s (%s bytes) for %s", job_id, len(content), file.filename)
        return {"job_id": job_id, "queued_bytes": len(content), "filename": file.filename}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to enqueue file")
        raise HTTPException(status_code=500, detail=str(e))
