@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Build linux/amd64 + linux/arm64 images and push them to Docker Hub.
rem
rem Usage:
rem   publish-docker-images.bat
rem   publish-docker-images.bat api
rem   publish-docker-images.bat web
rem   publish-docker-images.bat --no-push
rem
rem Env:
rem   DOCKERHUB_USERNAME   default e54385991
rem   IMAGE_TAG            default latest
rem   DOCKER_BUILDER       default upkk-multi
rem   DOCKER_PLATFORMS     default linux/amd64,linux/arm64

cd /d "%~dp0"

set "API_REPO=upkk-cs2-server-manager"
set "WEB_REPO=upkk-cs2-server-manager-web"
if not defined DOCKERHUB_USERNAME set "DOCKERHUB_USERNAME=e54385991"
if not defined IMAGE_TAG set "IMAGE_TAG=latest"
if not defined DOCKER_BUILDER set "DOCKER_BUILDER=upkk-multi"
if not defined DOCKER_PLATFORMS set "DOCKER_PLATFORMS=linux/amd64,linux/arm64"
set "TARGET=all"
set "PUSH=1"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="-h" goto help
if /I "%~1"=="--help" goto help
if /I "%~1"=="/?" goto help
if /I "%~1"=="--no-push" set "PUSH=0" & shift & goto parse_args
if /I "%~1"=="api" set "TARGET=api" & shift & goto parse_args
if /I "%~1"=="web" set "TARGET=web" & shift & goto parse_args
if /I "%~1"=="all" set "TARGET=all" & shift & goto parse_args
echo ERROR: unknown argument: %~1
echo Try publish-docker-images.bat --help
exit /b 1

:help
echo Build linux/amd64 + linux/arm64 images and push them to Docker Hub.
echo.
echo Usage:
echo   publish-docker-images.bat
echo   publish-docker-images.bat api
echo   publish-docker-images.bat web
echo   publish-docker-images.bat --no-push
echo.
echo Env: DOCKERHUB_USERNAME, IMAGE_TAG, DOCKER_BUILDER, DOCKER_PLATFORMS
exit /b 0

:args_done
if not exist "%~dp0Dockerfile" (
    echo ERROR: run this from the repository root ^(missing Dockerfile^)
    exit /b 1
)
if not exist "%~dp0frontend\Dockerfile" (
    echo ERROR: missing frontend\Dockerfile
    exit /b 1
)

where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: docker is not installed or not on PATH
    exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: cannot talk to the Docker daemon. Start Docker Desktop first.
    exit /b 1
)
docker buildx version >nul 2>&1
if errorlevel 1 (
    echo ERROR: docker buildx is required
    exit /b 1
)

docker buildx inspect %DOCKER_BUILDER% >nul 2>&1
if errorlevel 1 (
    echo.
    echo ==^> creating buildx builder %DOCKER_BUILDER%
    docker buildx create --name %DOCKER_BUILDER% --driver docker-container --bootstrap
    if errorlevel 1 exit /b 1
)

if "%PUSH%"=="1" (
    echo.
    echo ==^> pushing as %DOCKERHUB_USERNAME% ^(run "docker login" first if this fails^)
)

if /I "%TARGET%"=="api" goto build_api
if /I "%TARGET%"=="web" goto build_web
if /I "%TARGET%"=="all" goto build_all
echo ERROR: unknown target: %TARGET%
exit /b 1

:build_all
call :publish %API_REPO% Dockerfile .
if errorlevel 1 exit /b 1
call :publish %WEB_REPO% frontend\Dockerfile frontend
if errorlevel 1 exit /b 1
goto done

:build_api
call :publish %API_REPO% Dockerfile .
if errorlevel 1 exit /b 1
goto done

:build_web
call :publish %WEB_REPO% frontend\Dockerfile frontend
if errorlevel 1 exit /b 1
goto done

:publish
set "IMAGE=docker.io/%DOCKERHUB_USERNAME%/%~1:%IMAGE_TAG%"
set "PUSH_FLAG="
if "%PUSH%"=="1" set "PUSH_FLAG=--push"
echo.
echo ==^> building %IMAGE% ^(%DOCKER_PLATFORMS%^)
docker buildx build --builder %DOCKER_BUILDER% --platform %DOCKER_PLATFORMS% --provenance=false --sbom=false -f "%~2" -t "%IMAGE%" %PUSH_FLAG% "%~3"
if errorlevel 1 exit /b 1
if "%PUSH%"=="1" (
    docker buildx imagetools inspect "%IMAGE%" --format "{{.Manifest.Digest}}"
    if errorlevel 1 exit /b 1
)
exit /b 0

:done
echo.
echo ==^> done
if not "%PUSH%"=="1" exit /b 0
echo Pull on the host, then recreate ^(do not uninstall, do not delete volumes^):
if /I "%TARGET%"=="web" goto hint_web
echo   docker pull docker.io/%DOCKERHUB_USERNAME%/%API_REPO%:%IMAGE_TAG%
if /I "%TARGET%"=="api" exit /b 0
:hint_web
echo   docker pull docker.io/%DOCKERHUB_USERNAME%/%WEB_REPO%:%IMAGE_TAG%
exit /b 0
