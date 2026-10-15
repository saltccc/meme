#!/bin/sh
set -e
python admin.py &
exec python server.py
