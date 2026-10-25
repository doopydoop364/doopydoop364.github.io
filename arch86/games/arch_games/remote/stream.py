"""Per-VM capture process shared by that VM's listeners; started on first listener, stopped on last."""

import asyncio
import contextlib
import shutil
import time

FRAME_MS = 20


class StreamError(Exception):
    pass


def capture_argv(vm, which=shutil.which):
    """Argument array for capturing exactly this VM's sink monitor. No shell is involved."""
    custom = vm.get("capture_command")
    if custom:
        return [str(part).format(source=vm["source"], rate=vm["rate"], channels=vm["channels"]) for part in custom]
    if which("parec"):
        return ["parec", "--device=" + vm["source"], "--format=s16le", "--rate=%d" % vm["rate"],
                "--channels=%d" % vm["channels"], "--latency-msec=%d" % vm["latency_ms"], "--raw"]
    if which("pw-record"):
        return ["pw-record", "--target=" + vm["source"], "--rate=%d" % vm["rate"], "--channels=%d" % vm["channels"],
                "--format=s16", "--latency=%dms" % vm["latency_ms"], "-P", "stream.capture.sink=true", "-"]
    raise StreamError("Neither parec nor pw-record is installed on this host")


class Listener:
    def __init__(self, depth=50):
        self.queue = asyncio.Queue(maxsize=depth)
        self.dropped = 0
        self.closed = asyncio.Event()
        self.error = None

    def push(self, chunk):
        if self.queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()  # keep latency bounded: drop the oldest frame
            self.dropped += 1
        self.queue.put_nowait(chunk)


class VMStream:
    def __init__(self, name, vm, which=shutil.which):
        self.name, self.vm, self.which = name, vm, which
        self.listeners = set()
        self.process = None
        self.task = None
        self.state = "idle"
        self.error = None
        self.started_at = None
        self.bytes_sent = 0
        self.starts = 0
        self.frame_bytes = vm["rate"] * vm["channels"] * 2 * FRAME_MS // 1000

    async def add(self, listener):
        self.listeners.add(listener)
        if self.state in {"idle", "failed"}:
            try:
                await self._start()
            except BaseException:
                self.listeners.discard(listener)
                raise

    async def remove(self, listener):
        self.listeners.discard(listener)
        if not self.listeners:
            await self._stop()

    async def _start(self):
        argv = capture_argv(self.vm, self.which)
        self.state, self.error = "starting", None
        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as exc:
            self.state, self.error = "failed", "cannot start capture: " + (exc.strerror or str(exc))
            raise StreamError(self.error) from None
        self.starts += 1
        self.started_at = time.monotonic()
        # A source that does not exist makes parec exit almost immediately; wait for the first data or an exit.
        first = asyncio.ensure_future(self.process.stdout.read(self.frame_bytes))
        done, _ = await asyncio.wait({first, asyncio.ensure_future(self.process.wait())}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if first in done and first.result():
            self.state = "running"
            self.task = asyncio.ensure_future(self._pump(first.result()))
            return
        first.cancel()
        message = await self._explain_failure()
        await self._kill()
        self.state, self.error = "failed", message
        raise StreamError(message)

    async def _explain_failure(self):
        text = ""
        if self.process.returncode is not None or not self.process.stdout.at_eof():
            with contextlib.suppress(Exception):
                text = (await asyncio.wait_for(self.process.stderr.read(2048), 1)).decode(errors="replace")
        line = text.strip().splitlines()[-1] if text.strip() else ""
        if self.process.returncode is None:
            return "capture produced no audio within 5 seconds (is the VM playing to sink '{}'?)".format(self.vm["sink"]) if not line else line[:200]
        return ("capture source unavailable: " + line[:200]) if line else "capture exited with status {}".format(self.process.returncode)

    async def _pump(self, first):
        buffer = bytearray(first)
        try:
            while True:
                while len(buffer) >= self.frame_bytes:
                    frame = bytes(buffer[: self.frame_bytes])
                    del buffer[: self.frame_bytes]
                    self.bytes_sent += len(frame)
                    for listener in list(self.listeners):
                        listener.push(frame)
                chunk = await self.process.stdout.read(self.frame_bytes)
                if not chunk:
                    break
                buffer += chunk
        except asyncio.CancelledError:
            raise
        finally:
            if self.state == "running":
                self.state = "failed" if self.listeners else "idle"
                self.error = "capture ended unexpectedly" if self.listeners else None
                for listener in list(self.listeners):
                    listener.error = self.error
                    listener.closed.set()
                    listener.push(None)

    async def _kill(self):
        process, self.process = self.process, None
        if process is None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    async def _stop(self):
        task, self.task = self.task, None
        if self.state == "running":
            self.state = "idle"
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._kill()
        if self.state != "failed":
            self.state = "idle"

    def status(self):
        return {"state": self.state, "listeners": len(self.listeners), "error": self.error, "starts": self.starts,
                "source": self.vm["source"], "rate": self.vm["rate"], "channels": self.vm["channels"],
                "dropped_frames": sum(listener.dropped for listener in self.listeners), "capturing": self.process is not None}
