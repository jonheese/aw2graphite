FROM python:3.8.1-alpine
WORKDIR /usr/src/app
COPY . /usr/src/app
RUN set -eux \
    && apk add --no-cache --virtual .build-deps build-base \
        libressl-dev libffi-dev gcc musl-dev python3-dev \
    && pip install --upgrade pip setuptools wheel \
    && rm -rf /root/.cache/pip \
    && pip install -r /usr/src/app/requirements.txt \
    && rm -rf /root/.cache/pip
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
CMD ["python3", "aw2graphite-rt.py"]
