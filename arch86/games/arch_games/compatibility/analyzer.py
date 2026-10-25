"""Evidence-based predictions, with hard blockers separated from uncertainty."""

from dataclasses import asdict, dataclass, field
import hashlib
import json


STATUSES = ("Verified Working", "Promising", "Experimental", "Unlikely", "Unsupported", "Unknown")


@dataclass
class Report:
    status: str = "Unknown"
    confidence: str = "Low"
    architecture_match: str = "unknown"
    runtime: str = "unknown"
    graphics: str = "Unknown requirements"
    memory: str = "Unknown requirements"
    missing_dependencies: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    uncertainties: list = field(default_factory=list)
    fingerprint: str = ""

    def to_dict(self):
        return asdict(self)


def fingerprint(profile):
    stable = {
        "os": profile.get("os"),
        "arch": profile.get("os_architecture"),
        "wine": profile.get("wine"),
        "graphics": profile.get("graphics"),
        "ram_total": profile.get("ram_total"),
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def analyze(game, profile, executable=None, requirements=None, feedback=None):
    req = dict(requirements or {})
    binary = executable.to_dict() if hasattr(executable, "to_dict") else executable or {}
    report = Report(fingerprint=fingerprint(profile))
    os_name = binary.get("os", game.os)
    arch = binary.get("architecture", game.architecture)
    host = profile.get("os_architecture", "unknown")
    graphics = profile.get("graphics", {})
    wine = profile.get("wine", {})
    report.evidence.extend(binary.get("evidence", []))
    report.evidence.append(
        "Configured Pacman repository availability"
        if game.source == "pacman"
        else game.metadata.get("analysis_source", "Game metadata")
    )
    imports = set(binary.get("imports", []))
    directx = (
        "12"
        if "d3d12.dll" in imports
        else "11"
        if "d3d11.dll" in imports
        else "9"
        if "d3d9.dll" in imports
        else None
    )
    if directx:
        report.evidence.append("PE imports Direct3D " + directx + "; runtime use is not proven")
    if "vulkan-1.dll" in imports or req.get("renderer") in {"dxvk", "vkd3d"}:
        req["vulkan"] = True
    if "opengl32.dll" in imports:
        report.evidence.append("PE imports OpenGL; minimum version unknown")
    for dll in sorted(imports):
        if dll.startswith(("msvcp", "vcruntime", "mscoree", "d3dx")):
            report.uncertainties.append("Check runtime component: " + dll)
    report.uncertainties.append("Architecture alone does not prove game compatibility")
    hard, unlikely, uncertain = [], [], []
    if host not in {"i686", "x86_64"}:
        hard.append("Only Linux x86 target hosts are supported")
    if profile.get("os", "linux") != "linux":
        hard.append("This application targets Linux")
    if arch not in {"unknown", "i686", "x86_64"}:
        hard.append("Foreign executable architecture: " + arch)
    elif arch == "x86_64" and host == "i686":
        hard.append("64-bit executable requires a 64-bit operating system")
        report.architecture_match = "no"
    elif arch != "unknown":
        report.architecture_match = "yes" if arch == host else "runtime-dependent"
    else:
        uncertain.append("Executable architecture has not been measured")
    if os_name == "windows":
        report.runtime = "Wine (isolated per-game prefix)"
        if not wine.get("available"):
            hard.append("Wine is not installed")
            report.missing_dependencies.append("Wine")
        elif (
            arch == "i686"
            and host == "x86_64"
            and wine.get("win32") is not True
            and wine.get("wow64") is not True
        ):
            if wine.get("win32") is False and wine.get("wow64") is False:
                hard.append("Installed Wine cannot run PE32")
            else:
                uncertain.append("Wine PE32/WoW64 support has not been verified")
        elif arch == "x86_64" and wine.get("win64") is not True:
            if wine.get("win64") is False:
                hard.append("Installed Wine cannot run PE32+")
            else:
                uncertain.append("Wine 64-bit prefix capability has not been verified")
    elif os_name == "linux":
        report.runtime = "Native Linux"
        if arch == "i686" and host == "x86_64":
            uncertain.append("ELF32 loader and native library availability unverified")
        report.uncertainties.append("Native library/ABI and game data requirements may apply")
    elif os_name == "dos":
        report.runtime = "DOSBox"
        if not any(profile.get("runtimes", {}).get(name) for name in ["dosbox", "dosbox-staging"]):
            hard.append("DOSBox is not installed")
            report.missing_dependencies.append("DOSBox")
    elif os_name != "unknown":
        hard.append("Executable OS/ABI is not supported: " + os_name)
    else:
        uncertain.append("Operating system/executable format unknown")
    graphical = req.get("display", game.category != "Terminal")
    if graphical and not graphics.get("display"):
        hard.append("Graphical session is required but not running")
        report.missing_dependencies.append("Graphical session")
    accelerated = req.get("acceleration", directx in {"11", "12"})
    if accelerated:
        report.graphics = "3D acceleration needed or strongly indicated"
        if graphics.get("accelerated") is False:
            unlikely.append("Software rendering is unlikely to provide reasonable 3D performance")
        elif graphics.get("accelerated") is None:
            uncertain.append("Hardware acceleration has not been measured")
    elif req.get("graphics") == "2d" or game.category == "Terminal":
        report.graphics = "2D/terminal; performance still requires testing"
    else:
        uncertain.append("Graphics requirements unknown")
    if req.get("vulkan"):
        report.graphics += "; Vulkan required by selected renderer/imports"
        if graphics.get("vulkan") is False:
            hard.append("Vulkan is required by the selected method and is unavailable")
        elif graphics.get("vulkan") is None:
            uncertain.append("Vulkan capability unknown")
    ram = req.get("ram_bytes")
    if ram:
        report.memory = "Requires {} bytes; {} bytes available".format(
            ram, profile.get("ram_available", "unknown")
        )
        if profile.get("ram_total") is not None and ram > profile["ram_total"]:
            unlikely.append("Physical memory below known minimum")
        elif profile.get("ram_available") is not None and ram > profile["ram_available"]:
            unlikely.append("Currently available memory below stated minimum")
    else:
        report.uncertainties.append("Minimum RAM is unknown")
    for dep in req.get("missing_dependencies", []):
        report.missing_dependencies.append(str(dep))
        hard.append("Known missing dependency: " + str(dep))
    report.evidence.extend(hard + unlikely)
    report.uncertainties.extend(uncertain)
    if hard:
        report.status, report.confidence = "Unsupported", "High"
    elif unlikely:
        report.status, report.confidence = "Unlikely", "Medium"
    elif arch == "unknown" and game.source != "pacman":
        report.status = "Unknown"
    elif uncertain:
        report.status, report.confidence = "Experimental", "Low"
    else:
        report.status, report.confidence = "Promising", "Medium"
    if feedback and feedback.get("fingerprint") == report.fingerprint:
        result = feedback.get("result")
        report.evidence.append("Local user feedback: " + str(result))
        if result in {"works", "issues"} and not hard:
            report.status, report.confidence = "Verified Working", "High"
            report.uncertainties.append(
                "User-attested result; verification does not extend to other configurations"
            )
        elif result == "slow" and not hard:
            report.status, report.confidence = "Unlikely", "High"
        elif result == "fails" and not hard:
            report.status, report.confidence = "Experimental", "High"
    return report
