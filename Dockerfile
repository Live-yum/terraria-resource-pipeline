FROM golang:1.23-bookworm AS go-build
WORKDIR /src
ARG SOURCE_COMMIT=""
ARG SOURCE_CLEAN="false"
COPY go.mod ./
COPY cmd/ ./cmd/
COPY internal/ ./internal/
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X terraria-resource-pipeline/internal/pipeline.BuildCommit=${SOURCE_COMMIT} -X terraria-resource-pipeline/internal/pipeline.BuildClean=${SOURCE_CLEAN}" -o /trp ./cmd/trp

FROM mono:6.12 AS runtime-build
WORKDIR /build
COPY tools/RuntimeExtractor/*.cs ./
RUN mcs -langversion:7.2 -optimize+ -r:System.Web.Extensions -out:/RuntimeExtractor.exe *.cs

FROM mono:6.12
RUN useradd --uid 10001 --create-home extractor
COPY --from=go-build /trp /app/trp
COPY --from=runtime-build /RuntimeExtractor.exe /app/RuntimeExtractor.exe
ENV TRP_RUNTIME_HELPER=/app/RuntimeExtractor.exe HOME=/tmp LANG=C.UTF-8
USER extractor
WORKDIR /app
ENTRYPOINT ["/app/trp"]
