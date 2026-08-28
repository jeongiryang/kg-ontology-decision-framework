"""실제 브라우저로 챗봇 화면을 몰아 스크린샷과 DOM 실측을 남긴다.

`node --check` 로는 잡히지 않는 런타임 참조 오류를 잡기 위한 것이다. 2026-08-29 에
탐색 패널의 `ReferenceError` 가 화면에서는 "연결이 종료되었습니다" 로만 보여, 이
스크립트 없이는 원인을 찾지 못했다.

준비:
    bash scripts/setup_browser_verification.sh
    source .cache/browser-verify/env.sh

사용:
    uv run python scripts/verify_chat_ui.py "컴퓨터공학과 전공필수 과목은?" \
        --choice 1 --tag cse --out out/shots
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

DEFAULT_BASE = "http://127.0.0.1:8501/"
ANSWER = "#screen-answer.is-active"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--choice", type=int, default=None,
                        help="되묻기 선택지 인덱스(0부터). 없으면 첫 응답에서 멈춘다")
    parser.add_argument("--tag", default="run", help="스크린샷 파일 접두어")
    parser.add_argument("--out", default="out/shots", help="스크린샷 디렉터리")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--dark", action="store_true", help="다크 모드로 렌더")
    parser.add_argument("--reduced-motion", action="store_true")
    parser.add_argument("--timeout", type=int, default=240_000)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1500, "height": 1200},
            color_scheme="dark" if args.dark else "light",
            reduced_motion="reduce" if args.reduced_motion else "no-preference",
        )
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)

        page.goto(args.base, wait_until="networkidle")
        page.fill("#question", args.question)
        page.click("#submit")
        page.wait_for_timeout(6000)
        page.screenshot(path=str(out / f"{args.tag}-progress.png"), full_page=True)
        page.wait_for_selector(ANSWER, timeout=args.timeout)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out / f"{args.tag}-answer.png"), full_page=True)

        if args.choice is not None:
            page.locator("#choice-list button").nth(args.choice).click()
            page.wait_for_timeout(6000)
            page.screenshot(path=str(out / f"{args.tag}-progress2.png"), full_page=True)
            page.wait_for_selector(ANSWER, timeout=args.timeout)
            page.wait_for_timeout(2000)
            page.screenshot(path=str(out / f"{args.tag}-final.png"), full_page=True)

        # 모든 접힘 영역과 단계 카드를 펼친 화면
        page.evaluate("""() => {
          document.querySelectorAll('details').forEach(d => { d.open = true; });
          document.querySelectorAll('#answer-progress-steps button.step-toggle')
            .forEach(b => b.click());
        }""")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(out / f"{args.tag}-expanded.png"), full_page=True)

        report = page.evaluate("""() => ({
          screen: document.querySelector('.screen.is-active')?.id,
          stageCards: document.querySelectorAll('#answer-progress-steps .step').length,
          disclosures: document.querySelectorAll('#answer-progress-steps .step-disclosure').length,
          fiveWOneH: document.querySelectorAll('.stage-5w1h').length,
          graphNodes: document.querySelectorAll('#answer-exploration .graph-node').length,
          graphEdges: document.querySelectorAll('#answer-exploration .graph-edge-group').length,
          operatorSteps: document.querySelectorAll('.operator-step').length,
          playButton: !!document.querySelector('.graph-simulate'),
          truncatedLabels: [...document.querySelectorAll('.graph-node-name')]
            .map(n => n.textContent).filter(t => t.includes('…')),
        })""")
        report["consoleErrors"] = errors
        browser.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
