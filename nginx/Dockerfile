# Pinned to the exact version validated in prod (2026-07), not the
# floating nginx:alpine tag — see backend/Dockerfile's FROM comment for
# why (check-and-deploy.sh prunes base-image layers every deploy, so a
# floating tag re-resolves silently on the next unrelated commit).
FROM nginx:1.31.3-alpine

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /var/cache/nginx/civitas /var/cache/nginx/civitas-action /var/cache/nginx/civitas-photos /etc/nginx/cache-epoch \
    && chown -R nginx:nginx /var/cache/nginx/civitas /var/cache/nginx/civitas-action /var/cache/nginx/civitas-photos

COPY civitas.conf /etc/nginx/conf.d/civitas.conf
# Included by civitas.conf at server level and in every location that sets
# a header of its own (see the file for why).
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
# Which proxy may name the visitor (REAL_IP_FROM / REAL_IP_HEADER): written at
# every start by the image's entrypoint, and once here so the included file
# exists even when nginx is started some other way.
COPY --chmod=755 real-ip.sh /docker-entrypoint.d/15-civitas-real-ip.sh
RUN /docker-entrypoint.d/15-civitas-real-ip.sh

EXPOSE 8081

# The probe reaches the frontend through nginx, so it sends the self-fetch
# marker the site's middleware skips (frontend/src/middleware.ts): otherwise
# every probe is counted as a visit to "/".
HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=10s \
    CMD wget -q --spider -U "Civitas-Bot/1.0 (healthcheck)" http://localhost:8081/ || exit 1
