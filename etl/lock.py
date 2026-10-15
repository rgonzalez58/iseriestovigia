"""Lock por archivo. No usa os.kill (en Windows termina el proceso)."""
import os
import time
from pathlib import Path


class YaEnEjecucion(Exception):
    pass


class Lock:
    def __init__(self, ruta, caduca_horas=12):
        self.ruta = Path(ruta)
        self.caduca = caduca_horas * 3600
        self._fd = None

    def __enter__(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self._fd = os.open(self.ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - self.ruta.stat().st_mtime > self.caduca:
                        self.ruta.unlink()  # lock huérfano
                        continue
                except FileNotFoundError:
                    continue
                raise YaEnEjecucion(str(self.ruta))
        raise YaEnEjecucion(str(self.ruta))

    def __exit__(self, *a):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.ruta.unlink()
        except FileNotFoundError:
            pass
