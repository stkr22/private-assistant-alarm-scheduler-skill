FROM python:3.11

ENV PYTHONUNBUFFERED 1

ARG WHEEL_FILE=my_wheel.wh

# Copy only the wheel file
COPY dist/${WHEEL_FILE} /tmp/${WHEEL_FILE}

# Install the package
RUN pip install /tmp/${WHEEL_FILE} && \
    rm /tmp/${WHEEL_FILE}

RUN groupadd -r pythonuser && useradd -r -m -g pythonuser pythonuser

WORKDIR /home/pythonuser

USER pythonuser

ENV PRIVATE_ASSISTANT_CONFIG_PATH=template.yaml

ENTRYPOINT ["private-assistant-alarm-scheduler-skill"]
