FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir .
EXPOSE 8080
ENV FREESOUNDER_HOST=0.0.0.0 FREESOUNDER_PORT=8080
CMD ["freesounder-control-api"]
