"""승인된 assistant turn의 실제 traversal 순서를 브라우저에서 검증한다.

실행 중 hop을 중계하는 도구가 아니다. 최종 assistant turn에서 `그래프 탐색`을 열고
서버가 보낸 `traversal_order` 재생 상태만 관찰한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--choice", type=int, default=None)
    parser.add_argument("--tag", default="traversal")
    parser.add_argument("--out", default="out/traversal")
    parser.add_argument("--base", default="http://127.0.0.1:8501/")
    parser.add_argument("--timeout", type=int, default=240_000)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shots: list[dict[str, object]] = []
    errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on(
            "console",
            lambda message: errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base, wait_until="networkidle")
        page.fill("#question", args.question)
        page.click("#submit")
        answer = page.locator(
            ".conversation-message.is-assistant:not(.is-pending)"
        ).last
        answer.wait_for(timeout=args.timeout)

        if args.choice is not None and answer.locator(".turn-choices button").count():
            selector = ".conversation-message.is-assistant:not(.is-pending)"
            before = page.locator(selector).count()
            answer.locator(".turn-choices button").nth(args.choice).click()
            page.wait_for_function(
                "count => document.querySelectorAll(" + repr(selector) + ").length > count",
                before,
                timeout=args.timeout,
            )
            answer = page.locator(selector).last

        graph_disclosure = answer.locator("details", has_text="그래프 탐색")
        if not graph_disclosure.count():
            errors.append("승인된 traversal graph disclosure가 없습니다.")
        else:
            graph_disclosure.locator("summary").click()
            replay = graph_disclosure.get_by_role("button", name="탐색 순서 재생")
            replay.wait_for(timeout=10_000)
            replay.click()
            max_steps = graph_disclosure.locator(".traversal-list li").count()
            seen: set[int] = set()
            for _ in range(max(1, max_steps * 6)):
                active = graph_disclosure.locator(".graph-edge-group.is-active")
                if active.count():
                    order = int(active.first.get_attribute("data-order") or "0")
                    if order and order not in seen:
                        seen.add(order)
                        path = out / f"{args.tag}-{order:02d}.png"
                        graph_disclosure.screenshot(path=str(path))
                        shots.append({"order": order, "file": str(path)})
                if len(seen) >= max_steps:
                    break
                page.wait_for_timeout(100)

        page.screenshot(path=str(out / f"{args.tag}-final.png"), full_page=True)
        browser.close()

    print(
        json.dumps(
            {"shots": shots, "console_errors": errors},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
