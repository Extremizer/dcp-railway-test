FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn httpx
COPY stage10_2_offline /app/stage10_2_offline
COPY dcp_stage10_ownership_epoch.py dcp_stage10_1_mock_ownership_api.py dcp_stage10_1_secure_control.py /app/
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
