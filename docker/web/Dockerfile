ARG BASE_IMAGE=ghcr.io/scoringengine/scoringengine/base:latest
FROM ${BASE_IMAGE}

USER root

# Inject upload folder
RUN mkdir -p /var/uploads && chown -R engine:engine /var/uploads && \
    mkdir -p /var/check_outputs && chown -R engine:engine /var/check_outputs

USER engine

COPY bin/web /app/bin/web

# nginx talks the uwsgi protocol on :5000.  The extra HTTP socket is bound to
# loopback only so the container healthcheck can curl /health/ready without
# exposing an unencrypted, nginx-bypassing entrypoint on the docker network.
CMD ["sh", "-c", "python -m scoring_engine.sse_server & exec uwsgi --socket :5000 --http-socket 127.0.0.1:5001 --wsgi-file bin/web --master --processes 4 --threads 2 --listen 256 --harakiri 60 --harakiri-verbose --ignore-sigpipe --ignore-write-errors --disable-write-exception --thunder-lock --stats 0.0.0.0:9191 --stats-http --buffer-size 32768 --post-buffering 8192"]

EXPOSE 5000 8001
