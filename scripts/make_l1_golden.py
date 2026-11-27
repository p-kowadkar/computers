"""Write tests/golden/expected/l1_clean.json from the current cleaning code.

Frozen means committed and never regenerated without a decision record. Run it only when
a decision record (docs/decisions/) says the cleaning rules changed on purpose.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "golden"))

import _l1

if __name__ == "__main__":
    _l1.EXPECTED.parent.mkdir(parents=True, exist_ok=True)
    _l1.EXPECTED.write_text(json.dumps(_l1.current(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {_l1.EXPECTED}")
