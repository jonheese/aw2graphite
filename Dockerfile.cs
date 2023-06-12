FROM python:3.11.4-alpine
WORKDIR /usr/src/app
RUN set -eux \
    && pip install --upgrade pip setuptools wheel \
    && rm -rf /root/.cache/pip
COPY requirements.cs.txt /usr/src/app/requirements.txt
RUN set -eux \
    && pip install -r /usr/src/app/requirements.txt \
    && rm -rf /root/.cache/pip
COPY aw2graphite-cs.py /usr/src/app/aw2graphite-cs.py
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
CMD ["flask", "--app", "aw2graphite-cs", "run", "--host", "0.0.0.0"]
