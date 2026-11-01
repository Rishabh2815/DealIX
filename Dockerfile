FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY main.py ./main.py
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir .

FROM builder AS test
RUN /opt/venv/bin/pip install --no-cache-dir '.[test]'
COPY tests ./tests
RUN /opt/venv/bin/python -c "import main; assert main.app" && /opt/venv/bin/python -m compileall -q src tests && PYTHONPATH=/build:/build/src /opt/venv/bin/pytest -q

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/main.py ./main.py
EXPOSE 8000
CMD ["uvicorn", "dealix.api:app", "--host", "0.0.0.0", "--port", "8000"]
