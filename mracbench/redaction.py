"""Stream a provider's output to evidence without persisting its credential."""

import threading


class RedactedPipe:
    def __init__(self, pipe, destination, secret):
        self.pipe, self.destination, self.secret = pipe, destination, secret.encode("utf-8")
        self.error = None
        self.thread = threading.Thread(target=self._copy, daemon=True)
        self.thread.start()

    def _copy(self):
        pending = b""
        try:
            with self.pipe:
                while chunk := self.pipe.read1(8192):
                    pending += chunk
                    while len(pending) >= len(self.secret):
                        index = pending.find(self.secret)
                        if index >= 0:
                            self.destination.write(pending[:index] + b"[REDACTED]")
                            pending = pending[index + len(self.secret) :]
                        else:
                            safe = len(pending) - len(self.secret) + 1
                            self.destination.write(pending[:safe])
                            pending = pending[safe:]
                            break
                    # Credential validation excludes newlines, so a complete line is safe to flush.
                    end = pending.rfind(b"\n") + 1
                    if end:
                        self.destination.write(pending[:end])
                        pending = pending[end:]
                    self.destination.flush()
                self.destination.write(pending.replace(self.secret, b"[REDACTED]"))
                self.destination.flush()
        except (OSError, ValueError) as exc:
            self.error = exc

    def finish(self):
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise OSError("Provider output pipe remained open after process termination")
        if self.error:
            raise OSError("Could not persist redacted provider output") from self.error
