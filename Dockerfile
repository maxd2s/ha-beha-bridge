ARG BUILD_FROM
FROM $BUILD_FROM

# Install Python and deps
RUN apk add --no-cache python3 py3-pip && \
    pip3 install paho-mqtt requests --break-system-packages

# Copy data for add-on
COPY run.sh /
COPY beha_auth.py /
COPY beha_mqtt.py /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
