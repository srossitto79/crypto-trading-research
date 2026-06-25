"""Module entrypoint for `python -m axiom`."""

from axiom.migration.juddex_to_axiom import migrate_home_directory as migrate_juddex
from axiom.migration.forven_to_axiom import migrate_home_directory as migrate_forven

# Run migrations in order: juddex first, then forven (juddex is older format)
migrate_juddex()
migrate_forven()

from axiom.config import ensure_state_dir_bootstrapped

ensure_state_dir_bootstrapped()

from axiom.cli import cli


if __name__ == "__main__":
    cli()
