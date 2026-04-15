FROM node:20-alpine

WORKDIR /app

# Install deps first for layer caching.
COPY package.json package-lock.json* ./
RUN npm install --silent

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
