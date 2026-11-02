FROM python:3.11-slim

WORKDIR /app

COPY backend/ /app/

RUN pip install --no-cache-dir \
    fastapi==0.141.1 \
    uvicorn==0.52.4 \
    pydantic==2.13.5 \
    python-multipart==0.0.32 \
    opencc-python-reimplemented==0.1.7 \
    numpy==2.3.4 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

RUN mkdir -p /app/data

ENV STORIES_DATA_DIR=/app/data

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
