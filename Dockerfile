# Use Playwright’s official image with Chromium preinstalled
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

# Set working directory
WORKDIR /app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Environment variables
ENV HEADLESS=1
ENV MAX_REVIEW_PAGES=5
ENV PYTHONUNBUFFERED=1

# Expose port for Render
EXPOSE 10000

# Start the app with Gunicorn
CMD ["bash", "-lc", "gunicorn app:app --bind 0.0.0.0:$PORT --timeout 180"]
