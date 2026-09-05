FROM node:22-slim AS css-build

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY static/src/ static/src/
COPY templates/ templates/
RUN npm run build:css


FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=css-build /app/static/dist/ static/dist/

RUN SECRET_KEY=build-only python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
