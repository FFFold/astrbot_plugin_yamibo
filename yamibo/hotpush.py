"""热帖推送核心逻辑：增量差分状态机（纯函数，便于单测）。

全量日报（每天定时推完整榜单）与增量雷达（新进榜即推）解耦：
- 全量日报的调度在 scheduler；
- 本模块只负责增量雷达的「这次榜单相比上次有哪些新进榜的帖子」。
"""

from dataclasses import dataclass, field

from yamibo.models import HotItem


@dataclass
class IncrState:
    """增量去重状态。date 变化时 pushed_tids 清零（按天重置去重）。"""

    date: str
    pushed_tids: list[int] = field(default_factory=list)
    last_tids: list[int] = field(default_factory=list)


def compute_incremental(
    state: IncrState | None, items: list[HotItem], today: str
) -> tuple[IncrState, list[HotItem]]:
    """增量差分：返回 (新状态, 本次应推送的新进榜帖子)。

    规则：
    - 空榜单（解析回归 / 登录页渲染等异常情况）：保留旧基线、不推送、
      不更新状态——防止恢复后把整个榜单误判为新进榜而轰炸。
    - state 为 None 或从未成功建基线（首次运行 / KV 数据丢失）：只建基线不推送。
    - 同天：对比 last_tids 且不在 pushed_tids（当天推过不重推）。
    - 跨天：pushed_tids 清零，只对比 last_tids（昨天推过并掉榜、
      今天重新进榜的帖子可再推；一直未掉榜的帖子不算新进榜）。
    """
    tids = [i.tid for i in items]
    if not tids:
        if state is None:
            return IncrState(date=today), []
        return state, []
    if state is None or not state.last_tids:
        return IncrState(date=today, last_tids=tids), []
    if state.date == today:
        fresh = [i for i in items if i.tid not in state.last_tids and i.tid not in state.pushed_tids]
        pushed = state.pushed_tids + [i.tid for i in fresh]
    else:
        fresh = [i for i in items if i.tid not in state.last_tids]
        pushed = [i.tid for i in fresh]
    return IncrState(date=today, pushed_tids=pushed, last_tids=tids), fresh
