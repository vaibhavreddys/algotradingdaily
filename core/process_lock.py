"""
Universal Singleton Process Lock Primitive.

Provides OS-level kernel file locking across Linux and Windows to guarantee
that only a single instance of a given service (Trading Engine, Telegram Bot,
Watchdog, Auto-login) can execute concurrently:
  - On Linux/Unix: Uses kernel-level fcntl.flock(LOCK_EX | LOCK_NB)
  - On Windows: Uses msvcrt.locking(LK_NBLCK, 1) with non-blocking semantics
  - Automatic Kernel Release: If a process terminates abnormally (SIGKILL, crash, OOM),
    the operating system automatically releases the file lock descriptor (zero stale locks).
"""

import os
import sys
import tempfile
import datetime
from pathlib import Path
from typing import Optional


class AnotherInstanceRunningError(RuntimeError):
    """Raised when another instance of the requested service already holds the lock."""
    def __init__(self, service_name: str, pid: Optional[int] = None, lock_file: Optional[str] = None):
        self.service_name = service_name
        self.pid = pid
        self.lock_file = lock_file
        pid_info = f" (PID {pid})" if pid else ""
        msg = f"Another instance of '{service_name}' is already running{pid_info}."
        super().__init__(msg)


class SingletonLock:
    """
    Guarantees that only one process can hold the named lock at any given time.
    Supports context manager ('with SingletonLock("name"):') or explicit acquire/release.
    """

    def __init__(
        self,
        service_name: str,
        lock_dir: Optional[str] = None,
        raise_on_conflict: bool = True,
    ):
        self.service_name = str(service_name).strip()
        self.raise_on_conflict = raise_on_conflict

        # Clean sanitized filename
        clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self.service_name)

        if lock_dir:
            self.lock_dir = Path(lock_dir)
        elif os.getenv("SINGLETON_LOCK_DIR"):
            self.lock_dir = Path(os.getenv("SINGLETON_LOCK_DIR"))
        else:
            if sys.platform != "win32" and os.path.isdir("/tmp"):
                self.lock_dir = Path("/tmp")
            else:
                self.lock_dir = Path(tempfile.gettempdir()) / "shoonya_algo_locks"

        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.lock_dir / f"{clean_name}.lock"

        self._fp = None
        self._acquired = False

    @property
    def is_acquired(self) -> bool:
        """Returns True if the current process instance holds this lock."""
        return self._acquired

    def get_locked_pid(self) -> Optional[int]:
        """Reads the PID recorded inside the lock file, if present and readable."""
        if self._acquired:
            return os.getpid()

        if not self.lock_file.exists():
            return None

        # On Windows, byte 0 is locked by msvcrt; reading from offset 1 avoids PermissionError.
        # On Linux, fcntl.flock does not lock bytes, so reading from 0 or 1 works everywhere.
        try:
            with open(self.lock_file, "r", encoding="utf-8") as f:
                if sys.platform == "win32":
                    f.seek(1)
                content = f.read().strip()
                for line in content.splitlines():
                    cleaned = line.strip()
                    if cleaned and cleaned.isdigit():
                        return int(cleaned)
        except Exception:
            pass
        return None

    def acquire(self) -> bool:
        """
        Attempts to acquire the singleton lock non-blockingly.
        Returns True if acquired.
        Returns False (or raises AnotherInstanceRunningError if raise_on_conflict=True)
        if another process currently holds the lock.
        """
        if self._acquired:
            return True

        try:
            # Open file in read/write mode (or create if not exists)
            self._fp = open(self.lock_file, "a+", encoding="utf-8")
        except Exception as e:
            if self.raise_on_conflict:
                raise RuntimeError(f"Failed to open lock file '{self.lock_file}': {e}") from e
            return False

        locked = False
        if sys.platform != "win32":
            import fcntl
            try:
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (BlockingIOError, IOError):
                locked = False
        else:
            import msvcrt
            try:
                # Ensure at least 1 byte exists in file to lock byte 0
                self._fp.seek(0, os.SEEK_END)
                if self._fp.tell() == 0:
                    self._fp.write("#\n")
                    self._fp.flush()
                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except (OSError, IOError):
                locked = False

        if not locked:
            existing_pid = self.get_locked_pid()
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None
            self._acquired = False

            if self.raise_on_conflict:
                raise AnotherInstanceRunningError(
                    service_name=self.service_name,
                    pid=existing_pid,
                    lock_file=str(self.lock_file),
                )
            return False

        # Lock acquired! Overwrite file contents with current process metadata.
        # First byte '#' is reserved for msvcrt byte-0 lock on Windows.
        try:
            if sys.platform != "win32":
                self._fp.seek(0)
                self._fp.truncate(0)
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._fp.write(f"#\n{os.getpid()}\n{self.service_name}\n{now_str}\n")
                self._fp.flush()
            else:
                # On Windows byte 0 is locked, write metadata starting after byte 0
                self._fp.seek(1)
                self._fp.truncate(1)
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._fp.write(f"\n{os.getpid()}\n{self.service_name}\n{now_str}\n")
                self._fp.flush()
        except Exception:
            pass

        self._acquired = True
        return True

    def release(self) -> None:
        """Releases the lock and closes the file descriptor."""
        if not self._acquired or self._fp is None:
            return

        try:
            if sys.platform != "win32":
                import fcntl
                try:
                    fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            else:
                import msvcrt
                try:
                    self._fp.seek(0)
                    msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass

            self._fp.close()
        except Exception:
            pass
        finally:
            self._fp = None
            self._acquired = False

        # Attempt to clean up the lock file from disk
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def __del__(self):
        if getattr(self, "_acquired", False):
            self.release()
