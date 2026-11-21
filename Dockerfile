FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 EKA_DATA_DIR=/app/data DEMO_AUTH=true
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir --no-deps .
RUN useradd -m app && mkdir -p /app/data && chown -R app /app
USER app
# Build the synthetic corpus and index at image build time so the container starts instantly.
RUN eka generate-data && eka ingest
EXPOSE 8080
CMD ["sh", "-c", "eka serve --host 0.0.0.0 --port ${PORT:-8080}"]
