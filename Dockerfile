FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
# Chromium печатает PDF: отчёт по ссылке и документы с нуля. Без него эти кнопки падают.
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium

COPY . .

RUN useradd --create-home app && mkdir -p /data && chown -R app /app /data
USER app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
