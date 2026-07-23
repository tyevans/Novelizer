from __future__ import annotations
import asyncio
from pathlib import Path
import click
from rich.console import Console
from rich.table import Table
from novelizer.settings import (
    EffectiveSettings,
    StoryDirectory,
    StoryConfigError,
    TOMLFileError,
    create_story,
    global_config_path,
    is_story_dir,
    list_stories,
    load_effective_settings,
    migrate_flat_layout,
    update_global_config,
    write_global_config,
)
from novelizer.runtime import Runtime
from novelizer.director import commands
from novelizer.voices.loader import load_voice_pack
from novelizer.voices.models import VoicePack
from novelizer.store.models import Character

console = Console()


async def _with_runtime(settings, fn):
    rt = Runtime(settings)
    # CLI commands that only touch the store don't need the LLM runner.
    await rt.events.init()
    await rt.projector.init()
    await rt.read.init()
    await rt.projector.catch_up()
    # Same ProposalService construction path Runtime.start() uses, without the
    # LLM agents/runners a store-only CLI command doesn't need.
    from novelizer.canon.proposal_service import ProposalService
    rt.proposals = ProposalService(rt.events)
    try:
        return await fn(rt)
    finally:
        await rt.close()


def _validated_story(story_path: str) -> StoryDirectory:
    root = Path(story_path).expanduser()
    if not is_story_dir(root):
        raise click.ClickException(
            f"{root} is not a story directory (no story.toml or world.db). "
            "Check the path, or run `novelizer` without --story to pick or create one."
        )
    return StoryDirectory(root=root)


def _resolve_story(
    story_path: str | None,
    stories_root: Path,
    base: EffectiveSettings,
    confirm=click.confirm,
    global_path: Path | None = None,
) -> StoryDirectory:
    """Headless story resolution: --story -> last-opened -> legacy migration -> default."""
    if story_path:
        return _validated_story(story_path)
    if base.last_opened_story and is_story_dir(Path(base.last_opened_story)):
        return StoryDirectory(root=Path(base.last_opened_story))
    if (stories_root / "world.db").exists():
        if base.suppress_flat_migration_prompt:
            return StoryDirectory(root=stories_root)
        if confirm(
            f"Found legacy flat story at {stories_root}/world.db. "
            f"Migrate it into {stories_root}/default/?",
            default=True,
        ):
            return migrate_flat_layout(stories_root)
        update_global_config(path=global_path, suppress_flat_migration_prompt=True)
        return StoryDirectory(root=stories_root)  # legacy paths keep working
    default = stories_root / "default"
    if is_story_dir(default):
        return StoryDirectory(root=default)
    return create_story(default, title="default")


def _run_wizard_app() -> dict | None:
    from novelizer.tui.setup_wizard import SetupWizardApp

    return SetupWizardApp().run()


def _run_picker_app(
    stories, stories_dir: Path, last_opened: str | None, base: EffectiveSettings
):
    from novelizer.tui.story_picker import StoryPickerApp

    return StoryPickerApp(
        stories,
        stories_dir=stories_dir,
        last_opened=last_opened,
        default_voice_pack=base.voice_pack,
        default_prose_profile=base.prose_profile,
    ).run()


def _interactive_startup(
    story_path: str | None,
    run_wizard=None,
    run_picker=None,
) -> EffectiveSettings | None:
    """TUI boot: wizard when unconfigured, then story pick. None = user quit."""
    run_wizard = run_wizard or _run_wizard_app
    run_picker = run_picker or _run_picker_app
    if not global_config_path().exists():
        wizard_data = run_wizard()
        if wizard_data is None:
            return None
        write_global_config(wizard_data)
    base = load_effective_settings()
    stories_root = Path(base.default_stories_dir).expanduser()
    if story_path:
        story = _validated_story(story_path)
    else:
        if (stories_root / "world.db").exists() and not base.suppress_flat_migration_prompt:
            if click.confirm(
                f"Found legacy flat story at {stories_root}/world.db. "
                f"Migrate it into {stories_root}/default/?",
                default=True,
            ):
                migrate_flat_layout(stories_root)
            else:
                update_global_config(suppress_flat_migration_prompt=True)
            base = load_effective_settings()
        chosen = run_picker(list_stories(stories_root), stories_root, base.last_opened_story, base)
        if chosen is None:
            return None
        story = StoryDirectory(root=Path(chosen))
    update_global_config(last_opened_story=str(story.root))
    return load_effective_settings(story_dir=story)


@click.group(invoke_without_command=True)
@click.option("--story", "story_path", default=None, type=click.Path(), help="Path to a story directory.")
@click.pass_context
def cli(ctx, story_path: str | None):
    ctx.ensure_object(dict)
    from novelizer.logging_setup import configure_logging
    configure_logging()
    try:
        if ctx.invoked_subcommand is None:
            settings = _interactive_startup(story_path)
            if settings is None:
                return  # user quit the wizard or picker
            _launch_tui(settings)
            return
        base = load_effective_settings()
        stories_root = Path(base.default_stories_dir).expanduser()
        story = _resolve_story(story_path, stories_root, base)
        if global_config_path().exists():
            update_global_config(last_opened_story=str(story.root))
        ctx.obj["settings"] = load_effective_settings(story_dir=story)
    except (TOMLFileError, StoryConfigError, FileExistsError) as e:
        raise click.ClickException(str(e)) from e


def _launch_tui(settings: EffectiveSettings) -> None:
    from novelizer.tui.app import NovelizerApp

    async def _boot():
        rt = Runtime(settings)
        await rt.start()
        app = NovelizerApp(rt)
        try:
            await app.run_async()
        finally:
            await rt.close()

    asyncio.run(_boot())


@cli.command()
@click.argument("text")
@click.pass_context
def seed(ctx, text: str):
    """Inject a narrative seed as a director_signal.created event."""
    async def _run(rt: Runtime):
        await commands.seed(rt.events, text)
        console.print(f"[green]Seed injected:[/green] {text}")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def chapters(ctx):
    """List chapters by editorial status."""
    async def _run(rt: Runtime):
        chs = await rt.read.list_chapters()
        if not chs:
            console.print("No chapters yet.")
            return
        table = Table(title="Chapters")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Title")
        table.add_column("Status")
        for c in chs:
            table.add_row(c.id[:8], c.title, c.editorial_status.value)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("chapter_id")
@click.pass_context
def read(ctx, chapter_id: str):
    """Print a chapter's prose."""
    async def _run(rt: Runtime):
        ch = await rt.read.get_chapter(chapter_id)
        if not ch:
            console.print(f"[red]Chapter {chapter_id} not found.[/red]")
            return
        console.rule(ch.title)
        console.print(ch.prose)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def retcons(ctx):
    """List open contradiction flags."""
    async def _run(rt: Runtime):
        reqs = await rt.read.list_flags(category="contradiction", status="open")
        if not reqs:
            console.print("No open retcon requests.")
            return
        table = Table(title="Open Retcon Requests")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Description")
        table.add_column("Proposed Resolution")
        for r in reqs:
            table.add_row(r.id[:8], r.description, r.proposed_resolution)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


def format_voice_report(pack: VoicePack, characters: list[Character], active_profile: str | None) -> str:
    """Pure formatter: voice pack + character voice cards -> a plain-text report.

    Kept dependency-free of Click/Rich/ReadStore so it is unit-testable without
    a runner or a database — the `voices` command below is a thin wrapper that
    fetches its inputs and prints this string via Rich.
    """
    lines = [f"Voice pack: {pack.name}", ""]
    lines.append("Prose profiles:")
    for name, profile in pack.prose_profiles.items():
        marker = "* " if name == active_profile else "  "
        snippet = profile.casting_note.strip().replace("\n", " ")[:80]
        lines.append(f"{marker}{name}: {snippet}")
    lines.append("")
    lines.append("Agent personalities:")
    for agent, note in pack.agent_personalities.items():
        lines.append(f"  {agent}: {note.strip().replace(chr(10), ' ')[:80]}")
    voiced = [c for c in characters if c.voice]
    if voiced:
        lines.append("")
        lines.append("Character voices:")
        for c in voiced:
            lines.append(f"  {c.name}: {c.voice.strip().replace(chr(10), ' ')[:80]}")
    return "\n".join(lines)


@cli.command()
@click.option("--pack", "pack_path", default=None, help="Inspect a voice pack other than the active one.")
@click.pass_context
def voices(ctx, pack_path: str | None):
    """Show the active (or given) voice pack's profiles, agent personalities, and character voice cards."""
    settings = ctx.obj["settings"]
    path = pack_path or settings.voice_pack
    pack = load_voice_pack(path)
    active_name = settings.prose_profile if pack_path is None else None

    async def _run(rt: Runtime):
        characters = await rt.read.list_characters()
        console.print(format_voice_report(pack, characters, active_name))
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command("voice-scaffold")
@click.argument("profile_name")
@click.argument("description")
@click.option(
    "--pack", "pack_path", default="stories/user_pack.toml",
    help="User pack file to write into (defaults to stories/user_pack.toml; never the shipped default pack).",
)
@click.pass_context
def voice_scaffold(ctx, profile_name: str, description: str, pack_path: str):
    """Scaffold a new prose profile into a user voice pack from a one-line description.

    No LLM call: the description you pass becomes the profile's casting note verbatim.
    """
    from novelizer.voices.scaffold import scaffold_prose_profile
    try:
        written = scaffold_prose_profile(pack_path, profile_name, description)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return
    console.print(f"[green]Scaffolded profile '{profile_name}' into {written}[/green]")


@cli.command()
@click.argument("level")
@click.argument("agent", required=False)
@click.pass_context
def autonomy(ctx, level: str, agent: str | None):
    """Set the global autonomy level, or a per-agent override."""
    from novelizer.canon.autonomy import AutonomyLevel, AutonomyState

    async def _run(rt: Runtime):
        try:
            lvl = AutonomyLevel(level)
        except ValueError:
            console.print(f"[red]Unknown autonomy level:[/red] {level}")
            return
        current = await rt.read.get_autonomy_state()
        if agent:
            overrides = dict(current.overrides)
            overrides[agent] = lvl
            next_state = AutonomyState(global_level=current.global_level, overrides=overrides)
            await commands.autonomy(rt.events, next_state)
            console.print(f"[green]Autonomy for {agent} set to {lvl.value}[/green]")
        else:
            next_state = AutonomyState(global_level=lvl, overrides=current.overrides)
            await commands.autonomy(rt.events, next_state)
            console.print(f"[green]Global autonomy set to {lvl.value}[/green]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command("plan-resolution")
@click.argument("thread_id")
@click.argument("window_lo", type=int)
@click.argument("window_hi", type=int)
@click.option("--note", default="", help="Optional planned-payoff note.")
@click.pass_context
def plan_resolution(ctx, thread_id: str, window_lo: int, window_hi: int, note: str):
    """Set (or clear, with 0 0) a thread's resolution window."""
    async def _run(rt: Runtime):
        result = await commands.plan_thread_resolution(rt.events, rt.read, thread_id, window_lo, window_hi, note)
        # commands.plan_thread_resolution has no ok/error return type -- its
        # success strings always start with "resolution window" (see
        # novelizer/director/commands.py), everything else is a rejection.
        color = "green" if result.startswith("resolution window") else "yellow"
        console.print(f"[{color}]{result}[/{color}]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command("retarget")
@click.argument("target_chapters", type=int)
@click.pass_context
def retarget(ctx, target_chapters: int):
    """Retarget the active blueprint's chapter count."""
    async def _run(rt: Runtime):
        result = await commands.retarget_blueprint(rt.events, rt.read, target_chapters)
        # commands.retarget_blueprint has no ok/error return type -- its
        # success strings always start with "blueprint retargeted" (see
        # novelizer/director/commands.py), everything else is a rejection.
        color = "green" if result.startswith("blueprint retargeted") else "yellow"
        console.print(f"[{color}]{result}[/{color}]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command("plan-reveal")
@click.argument("secret_id")
@click.argument("window_lo", type=int)
@click.argument("window_hi", type=int)
@click.pass_context
def plan_reveal(ctx, secret_id: str, window_lo: int, window_hi: int):
    """Set (or clear, with 0 0) a secret's reveal window."""
    async def _run(rt: Runtime):
        result = await commands.plan_secret_reveal(rt.events, rt.read, secret_id, window_lo, window_hi)
        # commands.plan_secret_reveal has no ok/error return type -- its
        # success strings always start with "reveal window" (see
        # novelizer/director/commands.py), everything else is a rejection.
        color = "green" if result.startswith("reveal window") else "yellow"
        console.print(f"[{color}]{result}[/{color}]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def proposals(ctx):
    """List pending (open) proposals."""
    async def _run(rt: Runtime):
        props = await rt.read.list_proposals(status="open")
        if not props:
            console.print("No pending proposals.")
            return
        table = Table(title="Pending Proposals")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Agent")
        table.add_column("Target Event")
        for p in props:
            table.add_row(p.id[:8], p.proposing_agent, p.target_event_type)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("proposal_id")
@click.pass_context
def approve(ctx, proposal_id: str):
    """Approve a pending proposal — appends its target event + proposal.approved."""
    async def _run(rt: Runtime):
        result = await commands.approve(rt.proposals, rt.read, proposal_id)
        console.print(f"[green]{result}[/green]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("proposal_id")
@click.pass_context
def reject(ctx, proposal_id: str):
    """Reject a pending proposal — appends proposal.rejected."""
    async def _run(rt: Runtime):
        result = await commands.reject(rt.proposals, rt.read, proposal_id)
        console.print(f"[yellow]{result}[/yellow]")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


def main():
    cli()
