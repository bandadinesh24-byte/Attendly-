FROM python:3.12-slim
WORKDIR /project
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt
COPY backend /project/backend
COPY scripts /project/scripts
COPY alembic.ini /project/alembic.ini
ENV PYTHONPATH=/project/backend
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
