FROM node:22-alpine AS build

ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_STATELESS_ONLY=false
ARG VITE_DEMO_VIDEO_URL=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_STATELESS_ONLY=$VITE_STATELESS_ONLY
ENV VITE_DEMO_VIDEO_URL=$VITE_DEMO_VIDEO_URL

WORKDIR /app

COPY package.json tsconfig.json vite.config.ts ./
RUN npm install

COPY index.html ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
