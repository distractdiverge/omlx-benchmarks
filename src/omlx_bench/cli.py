"""CLI entry point: `uv run omlx-bench <command>`."""
import typer

app = typer.Typer(no_args_is_help=True, help=__doc__)


@app.command()
def models():
    """List models the oMLX server knows."""
    raise NotImplementedError


@app.command()
def plan():
    """Expand plan.toml into new pending rows in queue.csv."""
    raise NotImplementedError


@app.command()
def run(only_suite: str | None = None, only_model: str | None = None):
    """Run pending/failed steps one at a time, keeping the Mac awake."""
    raise NotImplementedError


@app.command()
def status():
    """Summarize queue.csv: pending / pass / fail counts and failures."""
    raise NotImplementedError


@app.command()
def retry(step_id: str):
    """Reset a step that exhausted its attempts back to pending."""
    raise NotImplementedError


settings_app = typer.Typer(help="Show, change and restore oMLX settings.")
app.add_typer(settings_app, name="settings")


@settings_app.command("show")
def settings_show(model: str | None = None):
    """Show a model's settings (or global settings if no model)."""
    raise NotImplementedError


@settings_app.command("set")
def settings_set(model: str, assignments: list[str]):
    """Set per-model settings: key=value ... (snapshotted first)."""
    raise NotImplementedError


@settings_app.command("global")
def settings_global(assignments: list[str]):
    """Set global settings: key=value ... (snapshotted first)."""
    raise NotImplementedError


@settings_app.command("restore")
def settings_restore(snapshot: str | None = None):
    """Restore settings from the latest (or a named) snapshot."""
    raise NotImplementedError
