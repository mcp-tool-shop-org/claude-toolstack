FROM python:3.12-slim

# ripgrep (local search) + tini (PID 1 reaping)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py

# Run as non-root
RUN useradd -m -u 10001 appuser
USER appuser

ENV PYTHONUNBUFFERED=1
EXPOSE 8088

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8088"]
