# Build on an older userspace instead of inheriting the host's glibc baseline.
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils gcc libgl1 libegl1 libglib2.0-0 libdbus-1-3 libfontconfig1 fonts-dejavu-core \
    libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
    libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
    libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libsm6 libice6 \
    p7zip-full patchelf file xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir \
    'PySide6==6.11.2' 'py7zr==1.1.4' 'pyinstaller==6.22.3' \
    'setuptools==84.2.1' wheel

# The independent CRC reference in the test suite compiles a small C program.
RUN apt-get update && apt-get install -y --no-install-recommends libc6-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
