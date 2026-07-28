# 마크다운 파서 및 로컬 LLM 환경 세팅 기록

* **작업 브랜치:** `feat/markdown-parser-test`
* **작업일:** 2026년 7월 (WSL2 / Ubuntu 24.04 LTS)
* **개발 환경:** RTX 4070 Ti (12GB VRAM), RAM 64GB, Python 3.12.3

---

## 1. 시스템 패키지 설치 (apt)
Ollama 압축 해제 및 파이썬 가상환경 헤더 지원을 위해 우분투 OS에 아래 패키지를 설치함.

```bash
sudo apt update && sudo apt install -y zstd python3-pip python3-venv python3-dev
```

* `zstd`: Ollama 설치 바이너리(`.tar.zst`) 압축 해제용
* `python3-pip / python3-venv / python3-dev`: 우분투 파이썬 기본 개발 모듈

---

## 2. 로컬 LLM 세팅 (Ollama & Qwen 2.5)
외부 API 비용 절감 및 보안 강화를 위해 로컬 LLM 런타임 구축.

* **런타임 엔진:** Ollama
* **설치 명령:** `curl -fsSL https://ollama.com/install.sh | sh`
* **바인딩 주소:** `http://127.0.0.1:11434`
* **다운로드 모델:** `qwen2.5:7b` (약 4.7GB)
  * **선정 사유:** 8B 이하 체급 중 구조화된 데이터(JSON/Markdown) 추출 및 코딩 성능 최상위. 4070 Ti 12GB VRAM 완벽 상주 가능.
* **모델 다운로드 명령:** `ollama pull qwen2.5:7b`

---

## 3. 파이썬 가상환경 및 패키지 관리 (uv)
우분투 24.04 PEP 668(외부 관리 환경) 정책에 맞춰 `.venv` 가상환경 내에서 `uv` 패키지 관리자를 통해 라이브러리 설치.

* **설치 명령:** `uv pip install pymupdf4llm ollama`
* **핵심 라이브러리 목록:**
  * `pymupdf4llm` (v1.28.0): PDF 레이아웃 분석 및 마크다운 정규화 변환
  * `pymupdf` / `pymupdf-layout`: PDF 파싱 엔진
  * `onnxruntime`: 표/레이아웃 분석용 AI 추론 엔진
  * `tabulate`: 마크다운 표 생성기
  * `ollama` (v0.6.2): 로컬 Ollama API 통신 클라이언트
  * `pydantic` / `pydantic-core`: LLM JSON 검증용 스키마 라이브러리
  * `httpx`: 비동기 HTTP 통신

---

## 4. 이슈 및 해결 기록 (Troubleshooting)
1. **Ollama 설치 중 `zstd` 누락 에러:** `sudo apt install zstd`로 해결.
2. **`pip install`시 PEP 668 에러 & `.venv` 내 `ensurepip` 누락:** 시스템 `pip` 대신 프로젝트에 이미 구축된 `uv` 패키지 관리자(`uv pip install`)를 활용하여 가상환경 오염 없이 깨끗하게 패키지 설치 완료.
