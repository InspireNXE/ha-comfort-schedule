# Run emu_test.py with plain Python (3.11+), the way emu.html runs it under Pyodide.
#   python tools/run_emu.py
# Prints every check and exits 1 if any failed. Used by CI.
import asyncio
import pathlib
import sys

tools = pathlib.Path(__file__).resolve().parent
integration = tools.parent / "custom_components" / "comfort_schedule"
names = ("const", "schedule", "health", "runtime", "learning", "suggestions", "controller")
scope = {"SOURCES": {n: (integration / f"{n}.py").read_text(encoding="utf-8") for n in names}}
exec(compile((tools / "emu_test.py").read_text(encoding="utf-8"), "emu_test.py", "exec"), scope)
asyncio.run(scope["run"]())
results = scope["results"]
print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
