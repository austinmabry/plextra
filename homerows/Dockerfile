FROM python:3.12-alpine
RUN pip install --no-cache-dir pyyaml==6.0.*
WORKDIR /app
COPY homerows.py .
USER nobody
ENV ROWS_FILE=/config/rows.yml
CMD ["python", "-u", "homerows.py"]
