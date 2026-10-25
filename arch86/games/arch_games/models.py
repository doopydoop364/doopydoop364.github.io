"""Shared records. Metadata is evidence, never executable configuration."""
from dataclasses import asdict, dataclass, field


@dataclass
class Game:
    id: str
    title: str
    source: str = "pacman"
    package: str = ""
    category: str = "Other"
    art: str = "*"
    description: str = ""
    installed: bool = False
    edition: str = ""
    build: str = ""
    os: str = "unknown"
    architecture: str = "unknown"
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)
