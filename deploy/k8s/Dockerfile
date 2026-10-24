# stasisd image for deploy/k8s (arm 10). stasisd is stdlib-only Python, so
# the image needs nothing beyond the interpreter. Pin the base by digest when
# you build for real.
FROM python:3.12-slim
COPY controller/stasisd.py /opt/stasis/stasisd.py
ENTRYPOINT ["python3", "/opt/stasis/stasisd.py"]
