# ---- build stage ----
FROM golang:1.22-alpine AS build
WORKDIR /app
ENV GOTOOLCHAIN=local
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o triage-rbac ./...

# ---- runtime stage ----
FROM gcr.io/distroless/base-debian12
WORKDIR /app
COPY --from=build /app/triage-rbac /app/triage-rbac
EXPOSE 4180
ENTRYPOINT ["/app/triage-rbac"]
