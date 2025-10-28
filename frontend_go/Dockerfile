# ---- build stage ----
FROM docker.io/library/golang:1.22-alpine AS build
WORKDIR /app
ENV GOTOOLCHAIN=local
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o hedi-frontend ./main.go


# ---- runtime stage ----
FROM gcr.io/distroless/base-debian12
WORKDIR /app

# Copy the binary and assets
COPY --from=build /app/hedi-frontend /app/hedi-frontend
COPY public /app/public
COPY auth /app/auth

# Expose both ports (HTTP redirect + HTTPS)
EXPOSE 8080 8443

ENTRYPOINT ["/app/hedi-frontend"]
