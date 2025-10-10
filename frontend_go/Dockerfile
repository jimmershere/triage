# syntax=docker/dockerfile:1
FROM docker.io/library/golang:1.23 as build
WORKDIR /app
COPY go.mod ./
RUN go mod download
COPY . .
ENV CGO_ENABLED=0
RUN go build -o /go-frontend

FROM gcr.io/distroless/base-debian12:nonroot
WORKDIR /app
COPY --from=build /go-frontend /app/go-frontend
COPY --from=build /app/public /app/public
COPY --from=build /app/templates /app/templates
USER nonroot:nonroot
ENV LISTEN_ADDR=:8080
ENV API_BASE=http://api:8000
ENV BASIC_AUTH=false
ENV USERS_FILE=/app/config/users.acl
EXPOSE 8080
ENTRYPOINT ["/app/go-frontend"]

FROM httpd:2.4
COPY deploy/httpd/conf.d/httpd.conf /usr/local/apache2/conf/httpd.conf
COPY deploy/httpd/conf.d/ssl.conf /usr/local/apache2/conf/extra/ssl.conf
COPY deploy/httpd/conf.d/vhosts-claims.conf /usr/local/apache2/conf/extra/vhosts-claims.conf
COPY deploy/httpd/auth /usr/local/apache2/conf/auth
COPY deploy/httpd/certs /usr/local/apache2/conf/certs
