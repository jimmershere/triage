# ---- build stage ----
FROM docker.io/library/golang:1.22-alpine AS build
WORKDIR /app
ENV GOTOOLCHAIN=local
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o hedi-rbac ./...

# ---- runtime stage ----
FROM gcr.io/distroless/base-debian12
WORKDIR /app
COPY --from=build /app/hedi-rbac /app/hedi-rbac
EXPOSE 4180
ENTRYPOINT ["/app/hedi-rbac"]
