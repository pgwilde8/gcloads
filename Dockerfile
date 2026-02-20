# Use a slim Python image to save RAM
FROM python:3.11-slim

# Set environment variables to keep Python from buffering and writing .pyc files
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /code

# Install system dependencies (needed for Postgres driver)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY ./app ./app
COPY ./inbound_listener.py ./inbound_listener.py

# Run the application on your favorite port
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8369"]
