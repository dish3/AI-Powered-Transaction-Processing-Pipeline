FROM python:3.11-slim

# Prevent Python from writing .pyc files to disk and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required for compilation and postgres client
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create uploads directory for shared CSV file storage
RUN mkdir -p uploads

# Expose FastAPI web port
EXPOSE 8000

# Default command (will run FastAPI web app)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
