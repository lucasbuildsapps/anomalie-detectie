"""Auto-discover plug-in detectors and visualizations from their folders."""
import importlib
import pkgutil


def _discover(package_name: str, base_cls) -> dict:
    package = importlib.import_module(package_name)
    instances = {}
    for _, name, _ in pkgutil.iter_modules(package.__path__):
        if name.startswith("_") or name == "base":
            continue
        module = importlib.import_module(f"{package_name}.{name}")
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, base_cls)
                and attr is not base_cls
            ):
                inst = attr()
                instances[inst.name] = inst
    return instances


def get_detectors() -> dict:
    from detectors.base import Detector
    return _discover("detectors", Detector)


def get_visualizations() -> dict:
    from visualizations.base import Visualization
    return _discover("visualizations", Visualization)
