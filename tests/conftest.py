import shutil
from pathlib import Path

import duckdb
import pytest
import yaml

from semlayer.core import Config
from semlayer.demo import run
from semlayer.registry import Registry
from semlayer.serve.service import Service

ROOT = Path(__file__).parent.parent


@pytest.fixture
def env(tmp_path):
    """Fully demo-built project in a temp dir: seeded db, certified registry."""
    shutil.copy(ROOT / "examples/jaffle_shop/seed.py", tmp_path / "seed.py")
    cfg = Config(db=str(tmp_path / "t.duckdb"), registry=str(tmp_path / "defs"),
                 query_log=str(tmp_path / "log.jsonl"), llm_provider="none")
    cfgfile = tmp_path / "semlayer.yaml"
    cfgfile.write_text(yaml.safe_dump(cfg.model_dump()))
    reg = Registry(cfg.registry)
    run(["--config", str(cfgfile)], cfg, reg)
    con = duckdb.connect(cfg.db, read_only=True)
    return type("Env", (), {"cfg": cfg, "cfgfile": cfgfile, "reg": reg, "con": con,
                            "svc": Service(reg, con), "tmp": tmp_path})
