# Zenas's Docassemble image: the official pinned image with the Zenas package baked in.
# Before 28 Oct these files were bind-mounted, one inside another read-only mount, and Docker
# refused to create the inner mount point ("read-only file system"), failing every deploy.
# Build context: agents/zenas. REGION_DIR picks the region's interviews (us or za); only the
# owning region's interviews are in its image.
FROM jhpyle/docassemble:1.10.14@sha256:5dc6feb56f69a912885ad5652d1754330f37c1a05331c5831e34e9d0c509492d
ARG REGION_DIR
ENV ZENAS_PKG=/usr/share/docassemble/local3.14/lib/python3.14/site-packages/docassemble/zenas
COPY docassemble/package ${ZENAS_PKG}
COPY core/drafting.py ${ZENAS_PKG}/legacy.py
COPY docassemble/${REGION_DIR} ${ZENAS_PKG}/data/questions/region
RUN test -n "${REGION_DIR}" && test -f ${ZENAS_PKG}/legacy.py && ls ${ZENAS_PKG}/data/questions/region/*.yml >/dev/null
