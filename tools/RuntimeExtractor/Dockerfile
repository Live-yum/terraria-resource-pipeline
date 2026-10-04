FROM mono:6.12 AS build
WORKDIR /src
COPY Program.cs PlayerDrawing.cs ./
RUN mcs -langversion:7.2 -optimize+ -r:System.Web.Extensions -out:/RuntimeExtractor.exe Program.cs PlayerDrawing.cs

FROM mono:6.12
RUN useradd --uid 10001 --create-home extractor
COPY --from=build /RuntimeExtractor.exe /app/RuntimeExtractor.exe
USER extractor
WORKDIR /app
ENTRYPOINT ["mono", "/app/RuntimeExtractor.exe"]
