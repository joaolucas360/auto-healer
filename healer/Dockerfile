FROM python:3.12-slim

LABEL maintainer="Joao Lucas" \
      version="1.0" \
      description="Healer App for Auto-Healer Demo"

RUN useradd -m -u 1000 appuser

WORKDIR /app

RUN pip install --no-cache-dir requests docker

COPY main.py .

RUN chown -R appuser:appuser /app

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import os; exit(0) if os.path.exists('main.py') else exit(1)"

CMD ["python", "main.py"]
