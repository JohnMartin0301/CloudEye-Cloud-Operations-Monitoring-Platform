# ----- Build stage -----
FROM python:3.13.2-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed by psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py database.py monitor.py metrics.py log_parser.py automation.py ./

# Copy frontend static files
COPY static/ ./static/

# Create a directory for the database to persist via volume
RUN mkdir -p /app/data

# Expose the FastAPI port
EXPOSE 8000

# Run the app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]