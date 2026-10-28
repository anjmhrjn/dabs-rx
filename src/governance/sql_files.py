"""Read and render the governance SQL files. No pyspark import, so tests can use it locally."""

import re
from string import Template


def statements(sql: str) -> list[str]:
    """Split on a semicolon at the end of a line, after dropping full-line -- comments."""
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*$", body, flags=re.MULTILINE) if s.strip()]


def render(sql: str, catalog: str, schemas: dict[str, str]) -> str:
    """Replace ${catalog} and ${<layer>} with backticked, fully qualified names."""
    return Template(sql).substitute(catalog=f"`{catalog}`", **{k: f"`{catalog}`.{v}" for k, v in schemas.items()})
