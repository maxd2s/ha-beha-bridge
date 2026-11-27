ARG BUILD_FROM
FROM $BUILD_FROM
RUN apk add --no-cache python3 py3-pip && pip3 install --no-cache-dir 'paho-mqtt==2.1.0' --break-system-packages
COPY bridge.py /bridge.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]
