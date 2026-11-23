"""Server admin commands (run on the Pi):

  python -m tt.cli gen-master-key
  python -m tt.cli create-admin <username>

The user-facing trading CLI (`trade ...`) lives in /cli and talks to the REST API.
"""
import asyncio
import getpass

import typer
from sqlalchemy import select

from tt.config import get_settings
from tt.core.security import (
    Vault,
    check_password_policy,
    generate_master_key,
    hash_password,
    new_totp_secret,
    totp_uri,
)
from tt.db.models import User
from tt.db.session import Database

app = typer.Typer(no_args_is_help=True)


@app.command("gen-master-key")
def gen_master_key():
    """Print a new Fernet key for TT_MASTER_KEY. Back it up: losing it loses all secrets."""
    typer.echo(generate_master_key())


@app.command("create-admin")
def create_admin(username: str):
    """Create the first admin. Prints the TOTP setup URI; confirm via POST /api/v1/auth/totp/setup."""
    password = getpass.getpass("Password (min 12 chars): ")
    check_password_policy(password)
    if password != getpass.getpass("Repeat password: "):
        raise typer.BadParameter("passwords differ")

    async def run():
        settings = get_settings()
        db = Database(settings.database_url)
        vault = Vault(settings.master_key)
        async with db.sessionmaker() as s:
            if (await s.execute(select(User).where(User.username == username))).scalar_one_or_none():
                raise typer.BadParameter("username exists")
            secret = new_totp_secret()
            s.add(User(username=username, password_hash=hash_password(password), role="admin",
                       totp_secret_enc=vault.encrypt(secret)))
            await s.commit()
        await db.dispose()
        typer.echo("Admin created. Add this to your authenticator app:")
        typer.echo(totp_uri(secret, username))
        typer.echo("Then confirm with a code: POST /api/v1/auth/totp/setup")

    asyncio.run(run())


if __name__ == "__main__":
    app()
