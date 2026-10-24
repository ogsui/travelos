FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

RUN npm run build \
    && addgroup -S app \
    && adduser -S app -G app \
    && chown -R app:app /app

ENV NODE_ENV=production
ENV PORT=3000
USER app

EXPOSE 3000

CMD ["npm", "start"]
