# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

FROM python:3.11-bookworm

# Override with a pinned release or package URL when reproducibility matters:
#   docker build --build-arg ASSERT_AI_PACKAGE='assert-ai==<version>' ...
ARG ASSERT_AI_PACKAGE=assert-ai
RUN pip install --no-cache-dir "${ASSERT_AI_PACKAGE}"

WORKDIR /app
COPY server.py /app/server.py
RUN chmod 0444 /app/server.py

USER 65534:65534
EXPOSE 8080
CMD ["python", "/app/server.py"]
