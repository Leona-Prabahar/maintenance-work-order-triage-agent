FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY approval_token.py server.py ./

# Streamable-HTTP MCP endpoint
EXPOSE 8001

# Run as a non-root user
RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "server.py"]
