# Two images from one file.
#
#   runtime -- what the Hub actually runs as. Installs requirements.txt only, so no test tooling is
#              present in the shipped image. It is deliberately the LAST stage, so a plain
#              `docker build .` produces it even if someone forgets --target.
#   test    -- runtime's dependencies plus requirements-dev.txt. Used only by the `test` service in
#              docker-compose.yml, never deployed.
#
# Splitting these is G5 condition H1: pytest was previously installed into the running image.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Both files are copied here so the test stage can install without re-copying, but only the runtime
# set is installed at this point. requirements-dev.txt is inert text in the runtime image.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt


FROM base AS test

RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

CMD ["pytest", "-q"]


FROM base AS runtime

COPY . .

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
