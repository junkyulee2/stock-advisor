"""Discord webhook notifier."""
from __future__ import annotations

import os
from typing import Optional

import requests

from .utils import setup_logger

logger = setup_logger(__name__)


def get_webhook_url(env_var: str = "DISCORD_WEBHOOK_URL") -> Optional[str]:
    return os.environ.get(env_var)


def send_message(content: str, webhook_url: Optional[str] = None) -> bool:
    url = webhook_url or get_webhook_url()
    if not url:
        logger.info("No Discord webhook configured; skipping notify.")
        return False
    try:
        r = requests.post(url, json={"content": content}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"Discord send failed: {e}")
        return False


def send_embed(
    title: str,
    description: str,
    color: int = 0x3498DB,
    fields: Optional[list[dict]] = None,
    webhook_url: Optional[str] = None,
) -> bool:
    url = webhook_url or get_webhook_url()
    if not url:
        return False
    embed = {"title": title, "description": description, "color": color}
    if fields:
        embed["fields"] = fields
    try:
        r = requests.post(url, json={"embeds": [embed]}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"Discord embed send failed: {e}")
        return False


def format_top3(recommendations: list[dict]) -> str:
    """Format top3 picks for Discord message."""
    lines = ["**Today's Top Picks**"]
    for i, r in enumerate(recommendations[:3], 1):
        lines.append(
            f"{i}. `{r['ticker']}` {r['name']} — **{r['total_score']:.1f}pt** "
            f"| size: {r.get('amount_krw', 0):,}원"
        )
        factors = r.get("factors", {})
        if factors:
            parts = [f"{k}: {v:.0f}" for k, v in factors.items()]
            lines.append("   └ " + " / ".join(parts))
    return "\n".join(lines)


def format_sell_alert(exit_order: dict, position: dict, current_price: float) -> str:
    ret_pct = (current_price / position["entry_price"] - 1) * 100
    ratio_pct = int(exit_order["sell_ratio"] * 100)
    return (
        f"🔔 **Sell Signal**: {position['name']} ({position['ticker']})\n"
        f"- return: {ret_pct:+.2f}%  (entry {position['entry_price']:,.0f} -> {current_price:,.0f})\n"
        f"- action: sell {ratio_pct}%\n"
        f"- reason: {exit_order['reason']}"
    )


def format_degradation_alert(position: dict, current_score_row: dict,
                             signs: list[dict], level: str) -> str:
    """Alert when factor breakdown shows the buy thesis is breaking down.

    `signs`: list of {key, label, entry, current, delta} from
    sell_signals_view.evaluate_degradation.
    """
    icon = "🚨" if level == "sell" else "⚠️"
    title = "매수 근거 붕괴" if level == "sell" else "매수 근거 약화"
    cur_total = float(current_score_row.get("total_score", 0) or 0)
    entry_total = float(position.get("entry_score", 0) or 0)

    lines = [
        f"{icon} **{title}**: {position['name']} ({position['ticker']})",
        f"- 종합 점수: {entry_total:.1f} → **{cur_total:.1f}** ({cur_total - entry_total:+.1f})",
        f"- 약화 신호 {len(signs)}개:",
    ]
    for s in signs:
        lines.append(f"  • {s['label']}: {s['entry']:.0f} → {s['current']:.0f} "
                     f"({s['delta']:+.0f})")
    return "\n".join(lines)
