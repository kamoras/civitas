FROM nginx:alpine

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /var/cache/nginx/civitas \
    && chown -R nginx:nginx /var/cache/nginx/civitas

COPY civitas.conf /etc/nginx/conf.d/civitas.conf

EXPOSE 8081

HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=10s \
    CMD wget -q --spider http://localhost:8081/ || exit 1
