# Use the official Python 3.12 image as a base.
FROM python:3.12-slim

# Set environment variables for encoding to prevent locale errors.
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Install necessary system tools
RUN apt-get update && \
    echo "deb http://deb.debian.org/debian trixie main" > /etc/apt/sources.list.d/trixie.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends -t trixie ghostscript && \
    apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-ita \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    qpdf \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install the required Python packages with a long timeout and pinned versions.
RUN pip install --no-cache-dir --timeout=600 \
    "ocrmypdf==16.10.0" \
    "pikepdf==8.13.0" \
    awslambdaric \
    boto3 \
    langdetect \
    pdf2image \
    opencv-python-headless

# Set the Lambda's working directory.
WORKDIR /var/task

# Copy the Python script from your local machine into the image.
COPY lambda_function.py .

# Define the entrypoint to run the function with the Lambda Runtime Interface Client.
ENTRYPOINT ["/usr/local/bin/python3", "-m", "awslambdaric"]

# Specify the handler in the correct format: filename.function_name
CMD ["lambda_function.lambda_handler"]
