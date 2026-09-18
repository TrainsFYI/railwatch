FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home railwatcher

WORKDIR /app
COPY pyproject.toml README.md ./
COPY railwatcher ./railwatcher
RUN python -m pip install --no-cache-dir .

USER 10001:10001
EXPOSE 8787
ENTRYPOINT ["railwatcher"]
CMD ["--host", "0.0.0.0", "--port", "8787", "--data", "/data", "--model", "/models/train.onnx"]
