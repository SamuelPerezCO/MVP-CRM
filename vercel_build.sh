#!/bin/sh
# The Vercel build step (vercel.json -> buildCommand: `sh vercel_build.sh`).
#
# In a file because vercel.json caps buildCommand at 256 characters -- the
# inline version tripped that and Vercel refused the deployment before the
# build even started. A file also has room to say why each line is here.
#
# Vercel's Django detection runs collectstatic on its own, AFTER this
# script: a buildCommand adds to the framework step rather than replacing it
# (the build log reads "Django 6.1.0 detected / Running collectstatic..."
# right after this finishes). So this script does not repeat it.
set -e

if [ "$VERCEL_ENV" = "preview" ]; then
  # Previews share the production database and have no business changing
  # its schema. Only an explicit "preview" opts out: an unset VERCEL_ENV
  # still migrates, so a system variable that isn't exposed can't quietly
  # turn migrations off -- the failure mode that cost an afternoon once.
  echo "preview build: skipping migrate (shares the production database)"
else
  # set -e above: a failed migrate fails the build, rather than deploying
  # code that queries a table the database does not have.
  python3 manage.py migrate --noinput
fi

# The origin that goes into the image links handed to WhatsApp. Printed so
# a wrong host -- one behind Vercel SSO, which Meta's servers cannot fetch
# from -- is visible in this log without anyone sending a message to find
# out. diffsettings lists the setting even when it is empty; core.W002 (run
# by migrate, above) warns loudly in that case.
python3 manage.py diffsettings 2>/dev/null | grep PUBLIC_BASE_URL \
  || echo "PUBLIC_BASE_URL could not be resolved"
