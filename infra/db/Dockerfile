# Postgres 16 + PostGIS 3.5 + pgvector (no single upstream image ships both).
FROM postgis/postgis:16-3.5
# Optional Debian mirror (e.g. http://mirrors.aliyun.com/debian) for slow networks;
# packages stay GPG-verified by apt regardless of mirror.
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s#http://deb.debian.org/debian#${APT_MIRROR}#g" /etc/apt/sources.list.d/*.sources /etc/apt/sources.list 2>/dev/null || true; \
    fi \
 # bullseye-pgdg was retired from apt.postgresql.org; it lives on in apt-archive
 && sed -i "s#http://apt.postgresql.org/pub/repos/apt#https://apt-archive.postgresql.org/pub/repos/apt#g" \
      /etc/apt/sources.list.d/pgdg.list 2>/dev/null || true \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/*
COPY init-extensions.sql /docker-entrypoint-initdb.d/20-extensions.sql
