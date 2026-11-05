# The bench client. It runs as a pod in the kind cluster, so Redis and Postgres are reached
# over the same cluster network (run.sh builds it and loads it into kind).
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
ENV PYTHONUNBUFFERED=1
CMD ["sleep", "infinity"]
