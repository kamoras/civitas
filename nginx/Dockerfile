# Pinned to the exact version validated in prod (2026-07), not the
# floating nginx:alpine tag — see backend/Dockerfile's FROM comment for
# why (check-and-deploy.sh prunes base-image layers every deploy, so a
# floating tag re-resolves silently on the next unrelated commit).
FROM nginx:1.31.3-alpine

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /var/cache/nginx/civitas \
    && chown -R nginx:nginx /var/cache/nginx/civitas

COPY civitas.conf /etc/nginx/conf.d/civitas.conf

EXPOSE 8081

HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=10s \
    CMD wget -q --spider http://localhost:8081/ || exit 1
