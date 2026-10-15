FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY leadpilot ./leadpilot
COPY static ./static
COPY catalog.json knowledge.json bot.py ./
RUN useradd --create-home leadpilot && mkdir /data && chown leadpilot /data
USER leadpilot
ENV DATABASE_PATH=/data/leads.db
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "leadpilot.app:app", "--host", "0.0.0.0", "--port", "8000"]
