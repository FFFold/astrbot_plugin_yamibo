from dataclasses import dataclass, field


@dataclass
class ThreadSummary:
    """版块/搜索列表项。"""

    tid: int
    title: str
    author: str = ""
    last_reply_time: str = ""
    reply_count: int = 0
    view_count: int = 0


@dataclass
class PostFloor:
    """一个楼层。images 为全尺寸绝对 URL。"""

    pid: int
    floor: int
    author_uid: int
    author_name: str
    time: str
    text: str
    images: list[str] = field(default_factory=list)
    is_op: bool = False


@dataclass
class ThreadContent:
    tid: int
    title: str
    author_uid: int
    author_name: str
    floors: list[PostFloor] = field(default_factory=list)

    def op_images(self) -> list[str]:
        """主楼全部图片（含附件），用于漫画打包。"""
        for f in self.floors:
            if f.is_op:
                return f.images
        return self.floors[0].images if self.floors else []


@dataclass
class SignStatus:
    signed_today: bool
    total_days: int = 0
    month_days: int = 0
    last_reward: str = ""
    last_time: str = ""


@dataclass
class HotItem:
    tid: int
    title: str
    author: str = ""
    date: str = ""
    reply_count: int = 0


@dataclass
class Subscription:
    id: str
    tid: int
    title: str
    op_uid: int
    op_name: str
    last_floor: int
    last_pid: int
    only_op: bool
    subscribers: list[str] = field(default_factory=list)
    created_at: int = 0
    paused: bool = False
    fail_count: int = 0
