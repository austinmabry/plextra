FROM python:3.12-alpine
RUN pip install --no-cache-dir pyyaml==6.0.*
COPY main.py /app/main.py
USER nobody
CMD ["python", "-u", "/app/main.py"]
