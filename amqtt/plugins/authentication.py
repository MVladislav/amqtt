from pathlib import Path
from typing import Any

from passlib.apps import custom_app_context as pwd_context

from amqtt.plugins.manager import BaseContext


class BaseAuthPlugin:
    """Base class for authentication plugins."""

    def __init__(self, context: BaseContext) -> None:
        self.context = context
        self.auth_config = self.context.config.get("auth") if self.context.config else None
        if not self.auth_config:
            self.context.logger.warning("'auth' section not found in context configuration")

    async def authenticate(self, *args: Any, **kwargs: Any) -> bool | None:  # noqa: ARG002
        """Logic for base Authentication. Returns True if auth config exists."""
        if not self.auth_config:
            # auth config section not found
            self.context.logger.warning("'auth' section not found in context configuration")
            return False
        return True


class AnonymousAuthPlugin(BaseAuthPlugin):
    """Authentication plugin allowing anonymous access."""

    async def authenticate(self, *args: Any, **kwargs: Any) -> bool:
        authenticated = await super().authenticate(*args, **kwargs)
        if authenticated:
            # Default to allowing anonymous
            allow_anonymous = self.auth_config.get("allow-anonymous", True) if self.auth_config is not None else True
            if allow_anonymous:
                self.context.logger.debug("Authentication success: config allows anonymous")
                return True

            session = kwargs.get("session")
            if session and session.username:
                self.context.logger.debug(f"Authentication success: session has username '{session.username}'")
                return True
            self.context.logger.debug("Authentication failure: session has no username")
        return False


class FileAuthPlugin(BaseAuthPlugin):
    """Authentication plugin based on a file-stored user database."""

    def __init__(self, context: BaseContext) -> None:
        super().__init__(context)
        self._users: dict[str, str] = {}
        self._read_password_file()

    def _read_password_file(self) -> None:
        """Read the password file and populates the user dictionary."""
        password_file = self.auth_config.get("password-file") if self.auth_config is not None else None
        if not password_file:
            self.context.logger.warning("Configuration parameter 'password-file' not found")
            return

        try:
            with Path(password_file).open("r") as file:
                self.context.logger.debug(f"Reading user database from {password_file}")
                for _line in file:
                    line = _line.strip()
                    if line and not line.startswith("#"):  # Skip empty lines and comments
                        parts = line.split(":", maxsplit=1)
                        if len(parts) == 2:
                            username, pwd_hash = parts
                            self._users[username] = pwd_hash
                            self.context.logger.debug(f"User '{username}' loaded")
                        else:
                            self.context.logger.warning(f"Malformed line in password file: {line}")
            self.context.logger.info(f"{len(self._users)} user(s) loaded from {password_file}")
        except FileNotFoundError:
            self.context.logger.warning(f"Password file '{password_file}' not found")
        except ValueError:
            self.context.logger.exception(f"Malformed password file '{password_file}'")
        except Exception:
            self.context.logger.exception(f"Unexpected error reading password file '{password_file}'")

    async def authenticate(self, *args: Any, **kwargs: Any) -> bool | None:
        """Authenticate users based on the file-stored user database."""
        authenticated = await super().authenticate(*args, **kwargs)
        if authenticated:
            session = kwargs.get("session")
            if not session:
                self.context.logger.debug("Authentication failure: no session provided")
                return False

            if not session.username:
                self.context.logger.debug("Authentication failure: no username provided in session")
                return None

            hash_session_username = self._users.get(session.username)
            if not hash_session_username:
                self.context.logger.debug(f"Authentication failure: no hash found for user '{session.username}'")
                return False

            if pwd_context.verify(session.password, hash_session_username):
                self.context.logger.debug(f"Authentication success for user '{session.username}'")
                return True

            self.context.logger.debug(f"Authentication failure: password mismatch for user '{session.username}'")
        return False
