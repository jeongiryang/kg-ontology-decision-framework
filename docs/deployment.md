# 로컬 시연 배포 및 정식 배포 계획

## 1. 현재 배포 상태

현재 정식 클라우드 배포는 **미구현**입니다. 이 문서의 실행 절차는 Windows 11 PC의 WSL2에서 Starlette 앱을 실행하고 ngrok HTTPS endpoint로 웹 포트 하나만 임시 공개하는 시연용 구성입니다.

- PC가 켜져 있고 WSL, Docker Desktop, Neo4j, Ollama, 챗봇과 ngrok이 모두 실행 중일 때만 접근할 수 있습니다.
- 외부 진입점은 ngrok이 전달하는 Starlette 웹 포트 `8501`뿐입니다.
- Neo4j Bolt `7687`, Neo4j Browser `7474`, Ollama `11434`와 PDF 파일은 로컬 PC에 유지합니다.
- GitHub Actions 기반 정식 CD, 고가용성, 자동 복구와 운영 인증은 아직 없습니다.

```text
외부 브라우저
→ ngrok HTTPS 개발용 주소
→ WSL 127.0.0.1:8501
→ Starlette evidence_chat
├── Ollama qwen2.5-coder:14b
├── Neo4j
└── Git 제외 19페이지 PDF
```

## 2. 사전 조건

다음 구성요소를 먼저 준비합니다.

- Windows 11과 WSL2 Ubuntu
- Docker Desktop과 WSL integration
- Verified KG가 적재된 로컬 Neo4j 컨테이너
- Ollama와 `qwen2.5-coder:14b`
- Python 3.12, `uv`, 잠금 파일과 동기화된 가상환경
- `tmux`
- ngrok 계정, ngrok Agent CLI와 authtoken

프로젝트·Neo4j·Ollama 설정은 [로컬 개발환경 구축 가이드](environment-setup.md), KG 적재는 [Neo4j 적재 가이드](neo4j-ingestion.md)를 먼저 따릅니다.

### ngrok 설치

아래 명령은 **WSL2 Ubuntu 셸**에서 실행합니다. 설치 방식은 변경될 수 있으므로 실행 전 [ngrok 공식 Linux 설치 페이지](https://ngrok.com/download/linux)를 확인합니다.

```bash
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
  | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null \
  && echo "deb https://ngrok-agent.s3.amazonaws.com bookworm main" \
  | sudo tee /etc/apt/sources.list.d/ngrok.list \
  && sudo apt update \
  && sudo apt install ngrok
```

버전과 설정을 확인하고 계정 dashboard의 authtoken을 로컬 ngrok 설정에 등록합니다.

```bash
ngrok version
ngrok config add-authtoken "<NGROK_AUTHTOKEN>"
ngrok config check
```

authtoken은 비밀값입니다. 셸 기록, 문서, 이슈, PR 또는 Git 파일에 복사하지 않습니다. 자세한 agent 명령은 [ngrok Agent CLI 문서](https://ngrok.com/docs/agent/cli)를 참고합니다.

## 3. 공개 시연용 환경 확인

실제 값은 Git에서 제외된 로컬 `.env`에만 설정합니다. 다음은 공개 시연 시 적용할 정책이며 자격증명은 [`.env.example`](../.env.example)의 빈 자리표시자를 사용해 별도로 채웁니다.

```dotenv
KG_CHAT_DEBUG=false
KG_CHAT_SHOW_QUERY_DETAILS=summary
KG_CHAT_MAX_CONCURRENT=1
KG_QUERY_TRACE_RAW_QUESTION=false
KG_QUERY_TRACE_FINGERPRINT=false
CHATBOT_HOST=127.0.0.1
CHATBOT_PORT=8501
```

`KG_CHAT_SHOW_QUERY_DETAILS=true`는 교수 시연에서 선택 스키마, EXPLAIN 승인 canonical Cypher와 정적 provenance 그래프를 보여 주기 위한 선택값입니다. 검증 전·실패한 Cypher, prompt, 모델 원문, 자격증명과 로컬 경로는 이 모드에서도 표시하지 않습니다.

다음 로컬 서비스 상태를 확인합니다.

```bash
docker ps
ollama list
curl -sS http://127.0.0.1:11434/api/tags
```

PDF 페이지 UI를 사용하려면 Git에서 제외된 실제 19페이지 발췌 PDF를 `KG_CHAT_PDF_PATH`로 지정합니다. health 응답은 경로나 SHA를 노출하지 않고 `pdf_mounted=true/false`만 반환합니다.

## 4. tmux로 챗봇 실행

WSL2 Ubuntu 셸에서 프로젝트로 이동해 챗봇 세션을 만듭니다.

```bash
cd ~/projects/kg-ontology-decision-framework
tmux new -s kg-chat
```

생성된 tmux 세션 안에서 실행합니다.

```bash
uv run python -m evidence_chat.server
```

서버 로그에서 비밀값을 출력하지 않습니다. 세션을 유지한 채 빠져나오려면 `Ctrl+B`를 누른 뒤 `D`를 누릅니다.

다른 WSL2 셸에서 상태를 확인합니다.

```bash
tmux ls
curl -sS http://127.0.0.1:8501/api/health
```

health가 HTTP 200을 반환하고 `service_ready=true`인지 확인합니다. PDF UI가 필요하면 `pdf_mounted=true`도 확인합니다. 현재 health는 Ollama API를 직접 검사하지 않으므로 실제 질의 전 Ollama 상태를 별도로 확인합니다.

## 5. tmux로 ngrok 실행

챗봇 health 확인 후 별도 WSL2 Ubuntu 셸에서 터널 세션을 만듭니다.

```bash
tmux new -s kg-tunnel
```

세션 안에서 Starlette 웹 포트만 공개합니다.

```bash
ngrok http http://127.0.0.1:8501
```

`Ctrl+B`, `D`로 분리합니다. 터널 상태와 일시적인 HTTPS 주소는 다음처럼 확인합니다.

```bash
tmux ls
tmux attach -t kg-tunnel
```

ngrok이 화면에 보여 주는 현재 HTTPS 주소를 시연 대상에게만 공유합니다. 주소를 README, 저장소 파일이나 PR에 고정 기록하지 않습니다. Neo4j와 Ollama 포트를 별도 ngrok endpoint로 만들지 않습니다.

## 6. 외부 검증

같은 로컬 네트워크의 우연한 우회를 피하려면 휴대전화 Wi-Fi를 끄고 모바일 데이터로 확인합니다.

1. ngrok HTTPS 주소를 엽니다.
2. 무료 plan 안내 화면이 표시되면 내용을 확인하고 `Visit Site`로 이동합니다.
3. 질문을 전송하고 실제 SSE 처리 타임라인이 누적되는지 확인합니다.
4. clarification이 필요하면 서버가 제공한 선택지를 선택해 재질의합니다.
5. 상세 모드에서 선택 스키마, 승인 Cypher와 정적 조회 그래프를 확인합니다.
6. Citation 원문과 발췌·원본·인쇄 페이지를 확인합니다.
7. PDF가 탑재됐다면 Citation의 원문 보기와 실제 텍스트 강조를 확인합니다.

시연 질문 예시:

```text
컴퓨터공학과 전공필수 과목은?
자료구조는 몇 학년 몇 학기에 개설돼?
```

질문 예시는 실행 편의를 위한 문구이며 서버의 질문별 분기나 정답값이 아닙니다.

## 7. 실행 유지와 운영 명령

시연 중 다음 항목이 모두 유지되어야 합니다.

- PC 전원과 안정적인 네트워크
- Windows 절전 비활성화
- WSL2 세션
- Docker Desktop과 Neo4j 컨테이너
- Ollama와 모델
- `kg-chat` tmux 세션
- `kg-tunnel` tmux 세션

노트북 덮개를 닫을 때 절전으로 전환되면 WSL·Docker·Ollama·ngrok이 중단될 수 있습니다. Windows 전원 설정의 절전과 덮개 동작을 시연 전에 확인합니다.

```bash
tmux ls
tmux attach -t kg-chat
tmux attach -t kg-tunnel
tmux kill-session -t kg-tunnel
tmux kill-session -t kg-chat
```

attach한 세션에서 현재 로그를 확인한 뒤 `Ctrl+B`, `D`로 다시 분리합니다. 시연 종료 시에는 외부 접근부터 닫기 위해 `kg-tunnel`을 먼저 종료하고, 필요하면 `kg-chat`을 종료합니다. Ollama와 Neo4지는 해당 도구의 정상 종료 절차를 사용합니다.

## 8. 보안 주의사항

- ngrok HTTPS URL은 공개 인터넷에서 접근 가능한 주소입니다. 현재 앱에는 정식 사용자 인증이 없습니다.
- URL을 아는 사용자가 질문을 보낼 수 있으므로 제한된 시간과 대상에만 공유합니다.
- 시연이 끝나면 `kg-tunnel` 세션을 즉시 종료합니다.
### 외부 시연에서 추적 공개 수준 낮추기

`KG_CHAT_SHOW_QUERY_DETAILS`는 `full`(기본) / `summary` / `off` 세 값을 받습니다. 기본값
`full`은 9단계 전 항목과 승인 Cypher 원문, 버려진 재시도 이력까지 보여 줍니다. 승인 Cypher는
공개 온톨로지에서 생성된 것이라 비밀이 아니지만, 외부 시연에서 질의 원문을 노출하고 싶지
않으면 `summary`로 낮춥니다. `summary`는 단계·소요시간·행 수·한국어 설명만 남기고 Cypher
원문과 질의 파라미터, 계획, 질문 원문 파생 값을 가립니다. 추적 패널 자체를 내리려면 `off`를
씁니다. 기존 `true`/`false` 값도 각각 `full`/`off`로 읽으므로 하위 호환됩니다.

```bash
KG_CHAT_SHOW_QUERY_DETAILS=summary uv run python -m evidence_chat.server
```

- `.env`, Neo4j 비밀번호, ngrok authtoken, 모델 API key를 Git에 추가하지 않습니다.
- Neo4j `7687`, Neo4j Browser `7474`, Ollama `11434`를 직접 터널링하지 않습니다.
- `KG_CHAT_DEBUG=false`와 질문 원문·fingerprint trace 비활성 정책을 유지합니다.
- 실제 ngrok 주소, 로컬 PDF 경로와 계정 정보를 문서나 runtime 응답에 기록하지 않습니다.
- 공개 전 Neo4j query 계정과 비밀번호 정책을 다시 확인합니다. Community Edition의 DB 역할 수준 읽기 전용 보장은 현재 PoC의 제한입니다.
- 무료 plan의 안내 화면과 한도는 변경될 수 있습니다. 숫자를 이 저장소에 고정하지 않고 [ngrok 공식 가격·한도 페이지](https://ngrok.com/pricing)를 확인합니다.

## 9. 제한사항

- PC가 꺼지거나 절전되면 서비스가 중단됩니다.
- 로컬 네트워크, 전원, WSL2와 Docker Desktop 상태에 의존합니다.
- 로컬 RTX 4070 Ti VRAM과 단일 Ollama 모델 요청 처리에 의존합니다.
- 기본 동시 chat 요청은 1개이며 여러 사용자는 대기할 수 있습니다.
- ngrok 무료 plan은 안내 화면과 사용 한도가 있을 수 있습니다.
- 일시적인 개발용 URL과 정책은 ngrok 또는 계정 설정에 따라 바뀔 수 있습니다.
- 이 구성은 정식 운영, 고가용성, 자동 재시작과 자동 복구를 제공하지 않습니다.

## 10. 장애 확인

| 증상 | 확인 사항 | 조치 |
|---|---|---|
| 외부 URL 접속 불가 | `kg-tunnel` 세션과 ngrok 화면 | `tmux attach -t kg-tunnel`로 종료·한도·주소 변경 확인 |
| `/api/health` 실패 | `kg-chat`, 포트 `8501`, lifespan | 챗봇 로그의 안전 오류 코드 확인 후 설정 보완 |
| `service_ready=false` 또는 HTTP 503 | `NEO4J_QUERY_*`, Neo4j 연결, LLM 설정 | ingestion 자격증명 fallback 없이 query 설정과 로컬 서비스 확인 |
| `pdf_mounted=false` | `KG_CHAT_PDF_PATH`, PDF 형식, 19페이지 | Git 제외 로컬 파일을 올바른 변수로 지정 후 서버 재시작 |
| Neo4j 연결 실패 | Docker 컨테이너, Bolt 포트, query 계정 | `docker ps`와 로컬 연결 확인; 외부 포트 공개 금지 |
| Ollama 관련 `SAFE_FAILURE` | Ollama 프로세스, API tags, 모델 목록 | `curl`과 `ollama list`로 확인; 서버 health만으로 통과 판단 금지 |
| tmux 세션 없음 | `tmux ls` | 누락된 세션을 해당 절차로 새로 시작 |
| 절전 후 중단 | Windows 전원, WSL·Docker·Ollama·tmux | 각 로컬 서비스를 순서대로 복구하고 health 재확인 |
| SSE 진행 표시 중단 | 브라우저 연결, 서버 로그, 단일 요청 대기 | 새로고침 전 진행 중 Ollama 요청이 남을 수 있음을 고려해 대기 후 재시도 |

오류를 공유할 때 `.env`, traceback 전체, URI, 로컬 경로, prompt나 모델 원문을 복사하지 않습니다.

## 11. 향후 정식 배포

> 상태: 계획 단계 / 미구현

정식 배포를 설계할 때 다음 범주를 이 문서에 추가합니다.

- Starlette 애플리케이션 호스팅과 프로세스 관리
- 관리형 또는 권한 분리가 가능한 Neo4j
- 원격 LLM provider와 provider 보안 검증
- PDF/object storage와 접근 정책
- 비밀값 관리
- 인증·접근 제어와 사용자별 rate limit
- 로그·메트릭·모니터링과 개인정보 보존 정책
- GitHub Actions CI/CD
- 배포·health check·rollback·재해 복구 절차
- 무료 한도, GPU와 데이터베이스 운영 비용

현재 특정 클라우드·데이터베이스·LLM 공급자를 최종 채택하지 않았습니다. 실제 정식 배포가 구현되면 서비스 URL, 환경변수, CI/CD, 접근 제어와 복구 절차를 검증 결과와 함께 이 섹션에 기록합니다.
