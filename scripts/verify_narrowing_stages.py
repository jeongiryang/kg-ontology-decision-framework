"""처리 중 화면의 그래프가 단계마다 실제로 달라지는지 스크린샷으로 남긴다.

`.narrowing-stage` 문구가 바뀔 때마다 한 장씩 찍는다. 문구가 바뀌지 않으면 찍지
않으므로, 남은 장수가 곧 "그림이 실제로 달라진 횟수"다. 단계 수를 채우려고
같은 그림을 여러 장 찍지 않는다.

준비:
    bash scripts/setup_browser_verification.sh
    source .cache/browser-verify/env.sh
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8501/"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--choice", type=int, default=None)
    parser.add_argument("--tag", default="stage")
    parser.add_argument("--out", default="out/stages")
    parser.add_argument("--timeout", type=int, default=240_000)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shots: list[dict] = []
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="networkidle")

        page.fill("#question", args.question)
        page.click("#submit")

        seen: list[str] = []
        deadline = page.evaluate("performance.now()") + args.timeout

        def capture() -> None:
            node = page.query_selector("#progress-exploration .narrowing-stage")
            if not node:
                return
            text = (node.inner_text() or "").strip()
            if not text or (seen and seen[-1] == text):
                return
            seen.append(text)
            index = len(seen)
            path = out / f"{args.tag}-{index:02d}.png"
            # 같은 화면을 덮어쓰지 않도록 캡처마다 새 파일을 쓴다.
            while path.exists():
                path = path.with_name(path.stem + "b" + path.suffix)
            page.screenshot(path=str(path))
            nodes = page.evaluate(
                "document.querySelectorAll('#progress-exploration .graph-node').length"
            )
            edges = page.evaluate(
                "document.querySelectorAll('#progress-exploration .graph-edge').length"
            )
            shots.append(
                {"n": index, "caption": text, "nodes": nodes, "edges": edges,
                 "file": str(path)}
            )
            print(f"[{index}] 노드 {nodes} 간선 {edges} · {text}")

        while page.evaluate("performance.now()") < deadline:
            capture()
            if page.query_selector("#screen-answer.is-active"):
                break
            page.wait_for_timeout(120)

        if args.choice is not None:
            buttons = page.query_selector_all("#choice-list button")
            if buttons:
                buttons[args.choice].click()
                seen.clear()
                deadline = page.evaluate("performance.now()") + args.timeout
                while page.evaluate("performance.now()") < deadline:
                    capture()
                    if page.query_selector("#screen-answer.is-active"):
                        break
                    page.wait_for_timeout(120)

        page.wait_for_timeout(1500)
        page.screenshot(path=str(out / f"{args.tag}-answer.png"), full_page=True)
        browser.close()

    print(json.dumps({"shots": shots, "console_errors": errors},
                     ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
