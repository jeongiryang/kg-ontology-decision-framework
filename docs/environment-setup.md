# 로컬 개발환경 구축 가이드

이 문서는 팀원이 동일한 버전과 도구 조합으로 프로젝트를 실행할 수 있도록 공식 로컬 개발환경 기준과 설치·검증 절차를 정의한다. 아래 명령은 개발환경을 구성할 때 실행할 예시이며, 이 문서를 작성하는 작업에서는 설치하거나 실행하지 않았다.

## 1. 공식 환경 기준

| 항목 | 기준 |
|---|---|
| 호스트 OS | Windows 11 |
| Linux 환경 | WSL2 |
| WSL 배포판 | Ubuntu 24.04 |
| IDE | VS Code + Remote WSL |
| 프로젝트 경로 | `~/projects/kg-ontology-decision-framework` |
| Python | CPython 3.12.3 |
| 패키지 관리자 | `uv` |
| 가상환경 | 프로젝트 루트의 `.venv/` |
| Neo4j 서버 | 2026.06.0 |
| Neo4j 실행 | Docker Desktop + WSL Integration |
| Neo4j Browser | `http://localhost:7474` |
| Neo4j Bolt | `bolt://localhost:7687` |
| GitHub 인증 | GitHub CLI, HTTPS |

Python과 Neo4j는 재현성을 위해 위 버전을 정확히 사용한다. Neo4j 서버 버전과 Python `neo4j` 드라이버 버전은 서로 다른 버전 체계를 사용하므로 혼동하지 않는다.

## 2. WSL2와 Ubuntu 24.04 설치

관리자 권한 Windows PowerShell에서 다음 명령을 실행한다.

```powershell
wsl --install -d Ubuntu-24.04
wsl --set-default-version 2
wsl --list --verbose
```

설치 후 Windows를 재시작하고 Ubuntu를 처음 실행하여 Linux 사용자명과 로컬 비밀번호를 설정한다. 이 계정 정보는 프로젝트 문서나 AI 작업 로그에 기록하지 않는다.

Ubuntu 터미널에서 배포판과 커널 정보를 확인한다.

```bash
lsb_release -a
uname -a
```

## 3. 기본 개발 패키지 설치

Ubuntu 패키지 목록을 갱신하고 기본 개발 도구를 설치한다.

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y build-essential
sudo apt install -y ca-certificates
sudo apt install -y curl
sudo apt install -y git
sudo apt install -y unzip
sudo apt install -y gh
```

설치 결과를 확인한다.

```bash
git --version
curl --version
gh --version
```

## 4. VS Code Remote WSL 연동

1. Windows에 VS Code를 설치한다.
2. VS Code 확장 마켓에서 `WSL` 확장을 설치한다.
3. Ubuntu 터미널에서 프로젝트 디렉터리로 이동한다.
4. WSL 컨텍스트로 VS Code를 연다.

```bash
cd ~/projects/kg-ontology-decision-framework
code .
```

VS Code 왼쪽 아래에 `WSL: Ubuntu-24.04`가 표시되는지 확인한다. Python 인터프리터는 프로젝트 루트의 `.venv/bin/python`을 선택한다.

## 5. Docker Desktop WSL 연동

1. Windows에 Docker Desktop을 설치한다.
2. Docker Desktop의 `Settings > General`에서 WSL 2 기반 엔진을 사용하도록 설정한다.
3. `Settings > Resources > WSL Integration`에서 Ubuntu 24.04 통합을 활성화한다.
4. Docker Desktop을 재시작한다.

Ubuntu 터미널에서 연동 상태를 확인한다.

```bash
docker version
docker context ls
docker run --rm hello-world
```

Docker 명령은 WSL 안에서 실행하되 Docker 데몬은 Docker Desktop이 제공한다. 프로젝트 디렉터리 안에 Neo4j 데이터 파일을 직접 생성하지 않고 named volume을 사용한다.

### 5.1 WSL Integration이 비활성인 환경의 대체 경로

WSL Integration을 활성화하지 않은 환경에서는 Ubuntu 안에 `docker` 실행 파일이 없고 다음 안내만 출력된다.

```text
The command 'docker' could not be found in this WSL 2 distro.
We recommend to activate the WSL integration in Docker Desktop settings.
```

이 경우 3절의 통합 활성화가 정식 해결책이다. 통합을 켜지 않고 임시로 진행해야 하면 Windows 쪽 `docker.exe`를 사용한다. `docker` 대신 `docker.exe`를 쓰는 점만 다르고 컨테이너 운영 명령은 8절과 동일하다.

```bash
docker.exe version --format '{{.Server.Version}}'
docker.exe ps --filter name=neo4j-db
```

Docker Desktop이 실행 중이 아니면 다음 오류가 발생하므로 Windows에서 Docker Desktop을 먼저 시작한다. 설치 위치는 환경에 따라 `%ProgramFiles%\Docker\Docker\` 또는 `%LOCALAPPDATA%\Programs\DockerDesktop\`이다.

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

`docker.exe`로 컨테이너를 띄워도 WSL에서 `localhost:7687`과 `localhost:7474`로 접속할 수 있다. 2026-08-10에 `wslinfo --networking-mode`가 `nat`인 환경에서 `127.0.0.1`과 `localhost` 양쪽 TCP 연결과 Bolt 접속이 성공함을 확인했다. 따라서 적재기의 로컬 URI 검사(`localhost` 또는 `127.0.0.1`의 7687 포트만 허용)를 우회하기 위한 추가 설정은 필요하지 않다.

`docker.exe`는 대체 경로이므로 계속 사용하지 않고 WSL Integration 활성화로 정리한다.

## 6. uv와 Python 3.12.3 설치

공식 설치 스크립트로 `uv`를 설치한 뒤 새 터미널을 열거나 안내된 PATH 설정을 적용한다.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

정확한 Python 패치 버전을 설치한다.

```bash
uv python install 3.12.3
uv python list
```

시스템 Python이나 다른 프로젝트의 Python 버전을 공통 기준으로 간주하지 않는다.

## 7. 프로젝트 가상환경 생성

프로젝트가 아직 없다면 GitHub CLI로 HTTPS 저장소를 복제한다. `<owner>`는 저장소 소유자 또는 조직명의 자리표시자다.

```bash
mkdir -p ~/projects
cd ~/projects
gh repo clone <owner>/kg-ontology-decision-framework
cd kg-ontology-decision-framework
```

이미 복제한 경우 프로젝트 루트로 이동한다.

```bash
cd ~/projects/kg-ontology-decision-framework
```

Python 3.12.3으로 프로젝트 전용 가상환경을 생성하고 활성화한다.

```bash
uv python install 3.12.3
uv venv --python 3.12.3
source .venv/bin/activate
python --version
```

공통 `pyproject.toml`과 `uv.lock`이 준비된 이후에는 잠금 파일을 기준으로 의존성을 동기화한다.

```bash
uv sync --locked
```

`uv sync --locked`가 잠금 파일 불일치로 실패하면 임의로 잠금 파일을 다시 만들지 말고 의존성 변경 담당자와 먼저 확인한다.

## 8. Neo4j 2026.06.0 실행

이미지 태그는 `latest`를 사용하지 않고 `neo4j:2026.06.0`으로 고정한다. 컨테이너를 삭제해도 데이터와 로그가 유지되도록 named volume을 연결한다.

```bash
docker run -d \
  --name neo4j-db \
  -p 7474:7474 \
  -p 7687:7687 \
  -v neo4j_data:/data \
  -v neo4j_logs:/logs \
  -e NEO4J_AUTH=neo4j/<your-password> \
  neo4j:2026.06.0
```

`<your-password>`는 사용자가 로컬에서 교체해야 하는 예시 자리표시자다. 실제 비밀번호를 문서, Git, Issue, PR, 스크린샷 또는 AI 로그에 기록하지 않는다. 셸 기록 노출이 우려되면 팀이 합의한 안전한 로컬 비밀 주입 방식을 사용한다.

컨테이너와 named volume을 확인한다.

```bash
docker ps --filter name=neo4j-db
docker volume inspect neo4j_data
docker volume inspect neo4j_logs
docker logs neo4j-db
```

Neo4j Browser는 `http://localhost:7474`, 애플리케이션 Bolt 연결은 `bolt://localhost:7687`을 사용한다.

컨테이너를 중지하거나 다시 시작할 때는 다음 명령을 사용한다.

```bash
docker stop neo4j-db
docker start neo4j-db
```

### 8.1 볼륨 구성 실제 확인

`-v neo4j_data:/data`와 `-v neo4j_logs:/logs` 없이 컨테이너를 만들면 Neo4j 이미지가 익명 볼륨을 자동 생성한다. 이 경우 이름이 해시 문자열이라 재생성 시 데이터를 다시 연결하기 어렵다. 실제 마운트를 확인한다.

```bash
docker inspect neo4j-db --format '{{range .Mounts}}[{{.Type}} {{.Name}} -> {{.Destination}}]{{end}}'
```

출력의 볼륨 이름이 `neo4j_data`, `neo4j_logs`가 아니라 64자 해시면 익명 볼륨이다. 2026-08-10 기준 이 환경의 `neo4j-db`는 익명 볼륨을 사용하고 있어 문서 규격과 다르다. 컨테이너를 재생성할 기회에 named volume으로 정리한다.

`docker volume ls`에 이름은 비슷하지만 어떤 컨테이너도 사용하지 않는 볼륨(`neo4j-data` 등)이 남아 있을 수 있다. 삭제 전에 사용 중인지 확인한다.

## 9. 환경변수 설정

프로젝트 루트의 `.env`는 로컬 전용이며 Git에 커밋하지 않는다. 변수 이름은 다음과 같이 통일하되 비밀번호 값은 비워 둔 예시만 문서에 남긴다.

```dotenv
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
```

로컬 파일을 편집한 뒤 파일 권한을 제한한다.

```bash
cd ~/projects/kg-ontology-decision-framework
code .env
chmod 600 .env
```

실제 계정명, 비밀번호, API 키, 토큰 또는 개인식별정보를 문서와 AI 로그에 복사하지 않는다.

## 10. Git과 GitHub CLI 설정

다음 자리표시자를 자신의 로컬 Git 정보로 교체한다. 실제 이메일은 이 문서나 AI 로그에 남기지 않는다.

```bash
git config --global user.name "<your-name>"
git config --global user.email "<your-github-email>"
git config --global init.defaultBranch main
```

GitHub CLI는 HTTPS와 웹 인증을 사용한다.

```bash
gh auth login --git-protocol https --web
gh auth status
git remote -v
```

토큰을 명령행 인수, Markdown 문서 또는 AI 로그에 직접 입력하지 않는다.

## 11. 설치 검증

WSL과 Ubuntu 버전을 확인한다.

```bash
wsl.exe --list --verbose
lsb_release -ds
```

프로젝트 경로와 Python 환경을 확인한다.

```bash
pwd
which python
python --version
uv --version
```

예상 Python 출력은 `Python 3.12.3`이다.

Docker와 Neo4j 상태를 확인한다.

```bash
docker version
docker ps --filter name=neo4j-db
docker exec neo4j-db neo4j --version
curl --fail http://localhost:7474
```

Git과 GitHub 인증을 확인한다.

```bash
git status --short --branch
gh auth status
```

Neo4j Browser에 접속하여 로컬에서 설정한 계정으로 로그인한 뒤 서버 연결 상태를 확인한다. 실제 비밀번호는 검증 로그에 복사하지 않는다.

## 12. 팀 공통 운영 규칙

- `.venv/`, `.env`, 원본 PDF, 로컬 DB 데이터는 Git에 커밋하지 않는다.
- `pyproject.toml`과 `uv.lock`을 공통 의존성 기준으로 사용한다.
- 의존성 변경 후 `uv.lock`을 함께 갱신한다.
- 팀원은 가능하면 `uv sync --locked`로 동일 환경을 구성한다.
- Neo4j 서버 버전과 Python 드라이버 버전을 혼동하지 않는다.
- 설치 또는 버전 기준이 변경되면 이 문서와 README 환경 요약을 함께 갱신한다.
- 실제 계정명·비밀번호·토큰은 문서와 AI 로그에 기록하지 않는다.
- 로컬에서만 필요한 파일과 생성물은 커밋 전에 `git status --short`와 `.gitignore`로 확인한다.
- 실행하지 않은 설치·테스트·검증을 완료했다고 기록하지 않는다.
- 공통 환경 변경은 관련 브랜치·커밋·AI 작업 로그에 변경 이유와 검증 결과를 남긴다.
