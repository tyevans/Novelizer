from __future__ import annotations
import click


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        click.echo("Novelizer M0 — TUI not yet wired. See docs/MILESTONES.md.")


def main():
    cli()
