FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 APP_ENV=production DB_FILE=/data/queueup.db
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY static static
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
