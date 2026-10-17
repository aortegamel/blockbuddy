"""Install/refresh the in-game bridge script into your Minecraft instance.

Copies bridge/minescript/agent_bridge.py into the instance's minescript/
folder (where Minescript looks for \\commands). Creates a minimal config.txt
with the system Python path only if Minescript hasn't generated one yet —
if Minescript complains about config on first launch, delete config.txt and
let the mod regenerate it, then re-run this script.

The installer preserves existing settings. It appends an autorun entry only
when the configuration has no active autorun[*] entry.

Set BLOCKBUDDY_MC_DIR to your instance's minecraft/ (or .minecraft/) folder.
"""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bridge" / "minescript" / "agent_bridge.py"


AUTORUN = 'autorun[*]=agent_bridge'


def main():
    mc = os.environ.get("BLOCKBUDDY_MC_DIR")
    if not mc:
        sys.exit("Set BLOCKBUDDY_MC_DIR to your Minecraft instance's minecraft/ folder")
    ms_dir = Path(mc) / "minescript"
    ms_dir.mkdir(exist_ok=True)
    dest = ms_dir / "agent_bridge.py"
    shutil.copy2(SRC, dest)
    print(f"installed {dest}")

    config = ms_dir / "config.txt"
    if not config.exists():
        python = shutil.which("python") or sys.executable
        config.write_text(f'python="{python}"\n{AUTORUN}\n', encoding="utf-8")
        print(f"wrote {config} (python={python})")
    else:
        text = config.read_text(encoding="utf-8")
        if not any(line.partition("=")[0].strip() == "autorun[*]" for line in text.splitlines()):
            with config.open("a", encoding="utf-8") as output:
                if text and not text.endswith("\n"):
                    output.write("\n")
                output.write(f"{AUTORUN}\n")
            print(f"appended autorun to {config} — bridge connects on every world join, no chat command needed")
        else:
            print(f"config exists with autorun, not touching: {config}")


if __name__ == "__main__":
    main()
