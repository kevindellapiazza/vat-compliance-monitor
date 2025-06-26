# Use Ubuntu to build the layer with OCRmyPDF
FROM ubuntu:22.04

# Install system dependencies
RUN apt-get update && \
    apt-get install -y python3 python3-pip ghostscript tesseract-ocr && \
    pip install --upgrade pip && \
    pip install ocrmypdf

# Create a layer-compatible folder structure
RUN mkdir -p /opt/python
RUN pip install ocrmypdf -t /opt/python

# Cleanup
RUN rm -rf /root/.cache

# Define output location for Lambda Layer ZIP
CMD cp -r /opt /layer && echo "Layer build complete. Run 'docker cp' to extract it."