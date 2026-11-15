FROM python:3.12-slim
LABEL org.opencontainers.image.source=https://github.com/bdlnaredi/Dukaan-POS
LABEL org.opencontainers.image.description="Dukaan: counter billing (POS) with GST that sends bills to TallyPrime"
WORKDIR /app
COPY app /app
COPY CHANGELOG.md /app/CHANGELOG.md
ENV DATA_DIR=/data PORT=2510 PYTHONUNBUFFERED=1 TZ=IST-5:30
EXPOSE 2510
VOLUME /data
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','2510')+'/api/hello')" || exit 1
CMD ["python", "/app/main.py"]
