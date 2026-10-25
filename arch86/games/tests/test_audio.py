from pathlib import Path
import subprocess
import tempfile
import unittest
import wave

from arch_games.audio.diagnose import diagnose
from arch_games.audio.probe import Probe, wine_driver
from arch_games.audio.tone import run_test, write_tone


def make_root(temp, cards=None, qemu=True, nodes=()):
    root = Path(temp)
    (root / "proc").mkdir(exist_ok=True)
    (root / "proc/cpuinfo").write_text("flags\t\t: fpu vme hypervisor sse\n")
    if cards is not None:
        (root / "proc/asound").mkdir(exist_ok=True)
        (root / "proc/asound/cards").write_text(cards)
    if qemu:
        (root / "sys/class/dmi/id").mkdir(parents=True, exist_ok=True)
        (root / "sys/class/dmi/id/sys_vendor").write_text("QEMU\n")
        (root / "sys/class/dmi/id/product_name").write_text("Standard PC (Q35 + ICH9, 2009)\n")
    if nodes:
        (root / "dev/snd").mkdir(parents=True, exist_ok=True)
        for n in nodes:
            (root / "dev/snd" / n).write_text("")
    return root


HDA = " 0 [Intel          ]: HDA-Intel - HDA Intel\n                      HDA Intel at 0xfebb0000 irq 36\n"


class Runner:
    """Maps argv[0] to (returncode, stdout, stderr); tools not in `tools` are 'not installed'."""

    def __init__(self, tools):
        self.tools, self.calls = tools, []

    def which(self, name):
        return "/usr/bin/" + name if name in self.tools else None

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc, out, err = self.tools[argv[0]](argv) if callable(self.tools[argv[0]]) else self.tools[argv[0]]
        return subprocess.CompletedProcess(argv, rc, out, err)


def PACTL_OK(argv):
    if argv[1] == "info":
        return (0, "Server Name: PulseAudio (on PipeWire 1.4.2)\nDefault Sink: alsa_output.hda\n", "")
    if argv[1] == "list":
        return (0, "52\talsa_output.hda\tPipeWire\ts16le 2ch 44100Hz\tRUNNING\n", "")
    return (0, "Mute: no\n", "")


class DiagnoseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def probe(self, runner, root, env=None):
        return Probe(root=root, run=runner, which=runner.which, environ=env if env is not None else {"XDG_RUNTIME_DIR": "/run/user/1000"})

    def layers(self, report):
        return {layer["layer"]: layer for layer in report["layers"]}

    def test_working_audio_matches_arch64_setup(self):
        root = make_root(self.tmp.name, HDA)
        r = Runner({"pactl": PACTL_OK, "aplay": (0, "card 0: Intel [HDA Intel], device 0: ALC269\n", ""), "systemd-detect-virt": (0, "kvm\n", "")})
        report = diagnose(self.probe(r, root))
        self.assertEqual(report["verdict"], "working")
        layers = self.layers(report)
        self.assertEqual(layers["guest-device"]["status"], "ok")
        self.assertEqual(layers["guest-audio-server"]["status"], "ok")
        self.assertTrue(report["virtualization"]["qemu"])

    def test_no_device_in_vm_gives_qemu_advice_not_server_blame(self):
        root = make_root(self.tmp.name, cards="--- no soundcards ---\n")
        r = Runner({"pactl": (1, "", "Connection refused")})
        report = diagnose(self.probe(r, root))
        self.assertEqual(report["verdict"], "no-audio-device")
        layer = self.layers(report)["guest-device"]
        self.assertEqual(layer["status"], "fail")
        self.assertIn("-device intel-hda", layer["fix"])
        self.assertIn("never edits host", layer["fix"])

    def test_device_present_but_server_not_running(self):
        root = make_root(self.tmp.name, HDA)
        r = Runner({"pactl": (1, "", "Connection refused")})
        report = diagnose(self.probe(r, root))
        self.assertEqual(report["verdict"], "device-without-server")
        self.assertEqual(self.layers(report)["guest-device"]["status"], "ok")
        self.assertEqual(self.layers(report)["guest-audio-server"]["status"], "fail")
        self.assertIn("pipewire-pulse", self.layers(report)["guest-audio-server"]["fix"])

    def test_pactl_missing_is_not_reported_as_server_down(self):
        root = make_root(self.tmp.name, HDA)
        report = diagnose(self.probe(Runner({}), root))
        self.assertEqual(self.layers(report)["guest-audio-server"]["status"], "warn")
        self.assertIn("not installed", self.layers(report)["guest-audio-server"]["detail"])

    def test_muted_default_sink(self):
        root = make_root(self.tmp.name, HDA)
        def pactl(argv):
            return (0, "Mute: yes\n", "") if argv[1] == "get-sink-mute" else PACTL_OK(argv)
        report = diagnose(self.probe(Runner({"pactl": pactl}), root))
        self.assertEqual(report["verdict"], "muted")

    def test_server_without_sinks(self):
        root = make_root(self.tmp.name, HDA)
        def pactl(argv):
            return (0, "", "") if argv[1] == "list" else (0, "Server Name: PipeWire\nDefault Sink: \n", "")
        report = diagnose(self.probe(Runner({"pactl": pactl}), root))
        self.assertEqual(report["verdict"], "server-without-sinks")

    def test_missing_runtime_dir_warning(self):
        root = make_root(self.tmp.name, HDA)
        report = diagnose(self.probe(Runner({}), root, env={}))
        self.assertEqual(self.layers(report)["session-environment"]["status"], "warn")

    def test_bare_metal_without_card_has_no_qemu_advice(self):
        root = make_root(self.tmp.name, cards="", qemu=False)
        (root / "proc/cpuinfo").write_text("flags : fpu sse\n")
        report = diagnose(self.probe(Runner({}), root))
        self.assertNotIn("intel-hda", self.layers(report)["guest-device"]["fix"])

    def test_wine_driver_from_registry(self):
        reg = Path(self.tmp.name) / "user.reg"
        reg.write_text('WINE REGISTRY Version 2\n\n[Software\\\\Wine\\\\Drivers] 1700000000\n"Audio"="pulse"\n\n[Software\\\\Other] 1\n"Audio"="x"\n')
        self.assertEqual(wine_driver(None, {"u": reg}), {"u": "pulse"})
        root = make_root(self.tmp.name, HDA)
        report = diagnose(self.probe(Runner({"pactl": (1, "", "")}), root), {"u": reg})
        self.assertEqual(self.layers(report)["wine-driver:u"]["status"], "warn")


class ToneTests(unittest.TestCase):
    def test_tone_is_bounded_and_valid(self):
        with tempfile.TemporaryDirectory() as d:
            length = write_tone(Path(d) / "t.wav", seconds=999)
            self.assertEqual(length, 10.0)
            with wave.open(str(Path(d) / "t.wav")) as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertAlmostEqual(w.getnframes() / w.getframerate(), 10.0, places=1)

    def test_prefers_pulse_then_falls_back_to_alsa(self):
        r = Runner({"paplay": (1, "", "Connection failure: Connection refused\n"), "aplay": (0, "", "")})
        result = run_test(Probe(run=r, which=r.which, environ={}), seconds=0.3)
        self.assertTrue(result["played"])
        self.assertEqual(result["backend"], "alsa")
        self.assertEqual(result["attempts"][0]["tool"], "paplay")
        self.assertIn("Connection refused", result["attempts"][0]["result"])
        self.assertIn("Only you can confirm", result["note"])

    def test_no_players(self):
        r = Runner({})
        result = run_test(Probe(run=r, which=r.which, environ={}), seconds=0.3)
        self.assertFalse(result["played"])
        self.assertTrue(all("not installed" in a["result"] for a in result["attempts"]))
        self.assertEqual(r.calls, [])

    def test_timeout_reported(self):
        def hang(argv):
            raise subprocess.TimeoutExpired(argv, 1)
        r = Runner({"aplay": hang})
        result = run_test(Probe(run=r, which=r.which, environ={}), seconds=0.3, backend="alsa")
        self.assertIn("timed out", result["attempts"][0]["result"])

    def test_invalid_backend(self):
        from arch_games.config import AppError
        with self.assertRaises(AppError):
            run_test(Probe(), backend="evil")
